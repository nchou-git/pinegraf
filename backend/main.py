from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import queue
import re
import threading
import time
from collections import OrderedDict, defaultdict, deque
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from backend.audit import (
    AdminLoginRequest,
    audit_events_response,
    install_audit_middleware,
    is_admin_request,
    login_admin,
)
from backend.config import get_settings
from backend.db.models import RawPage
from backend.db.store import KEEP_VERDICTS, SQLITE_WARNING, Store
from backend.pipeline.crawler import ProgressEvent, SiteCrawler
from backend.pipeline.extraction_audit import run_extraction_audit
from backend.pipeline.page_fetcher import MockPageFetcher, PageFetcher, TextBoilerplate
from backend.pipeline.parser import (
    MockExtractionClient,
    MockSynthesisClient,
    MockValidationClient,
    OpenAIExtractionClient,
    OpenAISynthesisClient,
    OpenAIValidationClient,
    Parser,
    _extraction_tier_mode,
    _parse_concurrency,
)
from backend.pipeline.query import (
    DeepQueryClient,
    MockDeepQueryClient,
    MockQueryClient,
    OpenAIQueryClient,
    QueryClient,
)
from backend.pricing import estimate_llm_dollars
from backend.request_logging import RequestLoggingMiddleware
from backend.resolution.embeddings import OpenAIEmbeddingClient
from backend.resolution.entity_resolver import reconcile_all
from backend.site_auth import SiteAuthMiddleware

logger = logging.getLogger(__name__)
DONE_SENTINEL = "__done__"
APP_STARTED_AT = datetime.now(UTC).isoformat()
USER_RATE_LIMIT = 60
USER_RATE_WINDOW_SECONDS = 60.0
RESEARCH_CACHE_TTL_SECONDS = 3600.0
RESEARCH_CACHE_MAX = 100
_user_rate_hits: dict[str, deque[float]] = defaultdict(deque)
_research_cache: OrderedDict[str, tuple[float, str]] = OrderedDict()


# ---------- request models ----------


class LookupRequest(BaseModel):
    name: str | None = None
    company: str | None = None
    class_year: str | None = None


class ResearchRequest(BaseModel):
    question: str
    mode: Literal["strict", "deep"] = Field(default="deep")


class ParseFilterRequest(BaseModel):
    url_pattern: str | None = None
    keywords: list[str] = Field(default_factory=list)
    limit: int | None = Field(default=None, ge=1)


class AuditRunRequest(BaseModel):
    sample_size: int = Field(default=30, ge=1, le=300)


class PipelineFinishRequest(BaseModel):
    status: Literal["complete", "failed", "canceled"]
    error_message: str = ""


class ResetExtractionRequest(BaseModel):
    confirmation: str


# ---------- background job plumbing ----------


@dataclass
class StageJob:
    name: str
    queue: queue.Queue[ProgressEvent | str] = field(default_factory=queue.Queue)
    running: bool = False
    cancel_requested: bool = False
    thread: threading.Thread | None = None


class JobCancelled(RuntimeError):
    pass


def _reset_job(job: StageJob) -> None:
    job.queue = queue.Queue()
    job.running = False
    job.cancel_requested = False
    job.thread = None


def _start_job(job: StageJob, target: Callable[[Callable[[ProgressEvent], None]], None]) -> str:
    if job.running:
        return "already_running"
    _reset_job(job)
    job.running = True

    def emit(event: ProgressEvent) -> None:
        if job.cancel_requested:
            raise JobCancelled(f"{job.name} canceled")
        job.queue.put(event)

    def worker() -> None:
        try:
            target(emit)
        except JobCancelled as exc:
            job.queue.put(ProgressEvent("done", {"error": str(exc), "canceled": True}))
        except Exception as exc:
            job.queue.put(ProgressEvent("done", {"error": f"{type(exc).__name__}: {exc}"}))
        finally:
            job.running = False
            job.queue.put(DONE_SENTINEL)

    job.thread = threading.Thread(target=worker, daemon=True, name=f"pinegraf-{job.name}")
    job.thread.start()
    return "started"


def _stop_job(job: StageJob) -> str:
    if not job.running:
        return "idle"
    job.cancel_requested = True
    return "canceling"


async def _event_generator(job: StageJob) -> AsyncIterator[bytes]:
    while True:
        try:
            event = job.queue.get_nowait()
        except queue.Empty:
            if not job.running and job.queue.empty():
                break
            await asyncio.sleep(0.05)
            continue
        if event == DONE_SENTINEL:
            break
        payload = json.dumps({"kind": event.kind, **event.data}, default=str)
        yield f"data: {payload}\n\n".encode("utf-8")


# ---------- builders ----------


def build_fetcher() -> PageFetcher:
    settings = get_settings()
    if settings.use_mock_fetch:
        return MockPageFetcher()
    return PageFetcher(boilerplate_provider=_boilerplate_for_host)


def _boilerplate_for_host(host: str) -> TextBoilerplate | None:
    row = store.get_host_boilerplate(host)
    if row is None:
        return None
    return TextBoilerplate(prefix=row.prefix, suffix=row.suffix)


def build_parser() -> Parser:
    settings = get_settings()
    if settings.use_mock_extract:
        return Parser(
            store=store,
            extractor=MockExtractionClient(),
            validator=MockValidationClient(),
            synthesizer=MockSynthesisClient(),
        )
    return Parser(
        store=store,
        extractor=OpenAIExtractionClient(api_key=settings.openai_api_key, store=store),
        validator=OpenAIValidationClient(
            api_key=settings.openai_api_key,
            model="gpt-5.4-mini",
            store=store,
        ),
        synthesizer=OpenAISynthesisClient(
            api_key=settings.openai_api_key,
            model="gpt-5.4",
            store=store,
        ),
        embedding_client=OpenAIEmbeddingClient(api_key=settings.openai_api_key, store=store),
    )


def build_query_client(mode: Literal["strict", "deep"]) -> QueryClient:
    settings = get_settings()
    if mode == "deep":
        if settings.use_mock_query:
            return MockDeepQueryClient(store)
        return DeepQueryClient(store=store, api_key=settings.openai_api_key, model="gpt-5.5")
    if settings.use_mock_query:
        return MockQueryClient(store)
    return OpenAIQueryClient(store=store, api_key=settings.openai_api_key, model="gpt-5.4-mini")


def _research_cache_key(question: str, mode: str) -> str:
    return f"{mode}:{' '.join(question.casefold().split())}"


def _cached_research_answer(question: str, mode: Literal["strict", "deep"]) -> str | None:
    key = _research_cache_key(question, mode)
    cached = _research_cache.get(key)
    if cached is None:
        return None
    cached_at, answer = cached
    if time.monotonic() - cached_at > RESEARCH_CACHE_TTL_SECONDS:
        _research_cache.pop(key, None)
        return None
    _research_cache.move_to_end(key)
    return answer


def _store_research_answer(question: str, mode: Literal["strict", "deep"], answer: str) -> None:
    key = _research_cache_key(question, mode)
    _research_cache[key] = (time.monotonic(), answer)
    _research_cache.move_to_end(key)
    while len(_research_cache) > RESEARCH_CACHE_MAX:
        _research_cache.popitem(last=False)


def answer_research_question(question: str, mode: Literal["strict", "deep"]) -> str:
    cached = _cached_research_answer(question, mode)
    if cached is not None:
        return cached
    answer = build_query_client(mode).answer_question(question).answer
    _store_research_answer(question, mode, answer)
    return answer


# ---------- app bootstrap ----------

app = FastAPI(title="Pinegraf")
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
settings = get_settings()
store = Store(settings.database_url)
store.init_db()
if store.is_sqlite:
    logger.warning(SQLITE_WARNING)
install_audit_middleware(app, store)
app.add_middleware(SiteAuthMiddleware)
app.add_middleware(RequestLoggingMiddleware)


@app.middleware("http")
async def user_rate_limit_middleware(request: Request, call_next):
    path = request.url.path
    if path != "/health" and not path.startswith("/admin"):
        client_ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        hits = _user_rate_hits[client_ip]
        while hits and now - hits[0] > USER_RATE_WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= USER_RATE_LIMIT:
            return Response("rate limit exceeded", status_code=429)
        hits.append(now)
    return await call_next(request)


crawl_job = StageJob("crawl")
parse_job = StageJob("parse")


# ---------- auth helpers ----------


def _require_admin(request: Request) -> None:
    if not is_admin_request(request):
        raise HTTPException(status_code=401, detail="admin auth required")


def _pages_for_parse_filter(payload: ParseFilterRequest) -> list[RawPage]:
    try:
        return store.list_pages_to_parse(
            url_pattern=payload.url_pattern,
            keywords=payload.keywords,
            limit=payload.limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _estimated_page_tokens(page_text: str) -> int:
    return max(0, math.ceil(len(page_text or "") / 4))


def _estimated_parse_dollars(
    total_tokens: int,
    page_count: int,
    *,
    tier_mode: str,
) -> float:
    triage_completion = page_count * 20
    extraction_completion = max(page_count * 120, math.ceil(total_tokens * 0.15))
    validation_completion = page_count * 80
    synthesis_completion = page_count * 120
    mini_cost = estimate_llm_dollars(
        "gpt-5.4-mini",
        prompt_tokens=total_tokens,
        completion_tokens=triage_completion + validation_completion,
    )
    if tier_mode == "frontier_only":
        extraction_cost = estimate_llm_dollars(
            "gpt-5.4",
            prompt_tokens=total_tokens,
            completion_tokens=extraction_completion,
        )
    else:
        extraction_cost = estimate_llm_dollars(
            "gpt-5.4-mini",
            prompt_tokens=total_tokens,
            completion_tokens=extraction_completion,
        )
        if tier_mode == "cascade":
            extraction_cost += estimate_llm_dollars(
                "gpt-5.4",
                prompt_tokens=math.ceil(total_tokens * 0.25),
                completion_tokens=math.ceil(extraction_completion * 0.25),
            )
    synthesis_cost = estimate_llm_dollars(
        "gpt-5.4",
        prompt_tokens=math.ceil(total_tokens * 0.2),
        completion_tokens=synthesis_completion,
    )
    return round(mini_cost + extraction_cost + synthesis_cost, 6)


def _pipeline_run_to_dict(row) -> dict[str, object] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "started_at": row.started_at.isoformat(),
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "status": row.status,
        "error_message": row.error_message,
    }


# ---------- public read endpoints ----------


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ok": True}


@app.get("/version")
async def version() -> dict[str, str]:
    version_file = Path("VERSION")
    git_sha = os.getenv("GIT_SHA", "").strip()
    if not git_sha and version_file.exists():
        git_sha = version_file.read_text(encoding="utf-8").strip().splitlines()[0]
    return {
        "git_sha": git_sha or "unknown",
        "deployed_at": os.getenv("DEPLOYED_AT", APP_STARTED_AT),
    }


@app.get("/stats")
async def public_stats() -> dict[str, int | float]:
    stats = store.admin_stats()
    cost = store.pipeline_cost_estimate()
    return {
        "alumni": stats["alumni"],
        "pages_crawled": stats["pages_crawled"],
        "pages_parsed": stats["pages_parsed"],
        "entities": stats["entities"],
        "connections": stats["connections"],
        **cost,
    }


@app.get("/profiles")
async def list_profiles() -> dict[str, object]:
    return {
        "profiles": [
            {
                "name": p.name,
                "entity_id": str(p.entity_id) if p.entity_id else None,
                "class_year": p.class_year,
                "current_company": p.current_company,
                "current_title": p.current_title,
                "past_companies": p.past_companies,
                "education": p.education,
                "bio_summary": p.bio_summary,
                "discovered_via": p.discovered_via,
                "last_parsed_at": p.last_parsed_at.isoformat() if p.last_parsed_at else None,
            }
            for p in store.list_profiles()
        ]
    }


@app.get("/connections")
async def list_connections() -> dict[str, object]:
    return {"connections": store.database_context(verdicts=KEEP_VERDICTS)["connections"]}


@app.get("/projects")
async def list_projects() -> dict[str, object]:
    return {"projects": store.database_context(verdicts=KEEP_VERDICTS)["projects"]}


@app.get("/facts")
async def list_facts() -> dict[str, object]:
    return {"facts": store.database_context(verdicts=KEEP_VERDICTS)["facts"]}


# ---------- user-facing endpoints ----------


@app.post("/lookup")
async def lookup(payload: LookupRequest, offset: int = 0, limit: int = 25) -> dict[str, object]:
    offset = max(0, offset)
    limit = min(max(1, limit), 100)
    name_q = (payload.name or "").strip().lower()
    company_q = (payload.company or "").strip().lower()
    year_q = (payload.class_year or "").strip().lower()

    def matches(p) -> bool:
        if name_q and name_q not in (p.name or "").lower():
            return False
        if company_q:
            haystack = " ".join(
                [(p.current_company or ""), " ".join(p.past_companies or [])]
            ).lower()
            if company_q not in haystack:
                return False
        if year_q and year_q not in (p.class_year or "").lower():
            return False
        return True

    matched = [p for p in store.list_profiles() if matches(p)]
    paged = matched[offset : offset + limit]
    return {
        "count": len(matched),
        "offset": offset,
        "limit": limit,
        "returned": len(paged),
        "results": [
            {
                "name": p.name,
                "entity_id": str(p.entity_id) if p.entity_id else None,
                "class_year": p.class_year,
                "current_company": p.current_company,
                "current_title": p.current_title,
                "past_companies": p.past_companies,
                "education": p.education,
                "bio_summary": p.bio_summary,
                "sources": [
                    {
                        "attribute_name": attr.attribute_name,
                        "attribute_value": attr.attribute_value,
                        "source": attr.source,
                        "source_url": attr.source_url,
                        "last_verified_at": (
                            attr.last_verified_at.isoformat() if attr.last_verified_at else None
                        ),
                    }
                    for attr in store.list_entity_attributes(entity_id=p.entity_id)
                ]
                if p.entity_id
                else [],
            }
            for p in paged
        ],
    }


@app.get("/entity/{entity_id}")
async def entity_detail(
    entity_id: str,
    request: Request,
    debug: bool = False,
    include_dropped: bool = False,
) -> dict[str, object]:
    if (debug or include_dropped) and not is_admin_request(request):
        raise HTTPException(status_code=401, detail="admin auth required")
    detail = store.entity_detail_with_options(
        entity_id,
        include_dropped=include_dropped,
        include_diagnostics=debug,
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="entity not found")
    return detail


@app.post("/research")
@limiter.limit("10/hour")
async def research(request: Request, payload: ResearchRequest) -> dict[str, str]:
    del request
    answer = answer_research_question(payload.question, payload.mode)
    return {"answer": answer, "mode": payload.mode}


@app.post("/research/stream")
@limiter.limit("10/hour")
async def research_stream(request: Request, payload: ResearchRequest) -> StreamingResponse:
    del request

    async def stream_answer() -> AsyncIterator[bytes]:
        answer = await asyncio.to_thread(answer_research_question, payload.question, payload.mode)
        if not answer.strip():
            yield b'data: {"kind":"empty"}\n\n'
        for token in re.split(r"(\s+)", answer):
            if token:
                yield f"data: {json.dumps({'kind': 'token', 'text': token})}\n\n".encode()
                await asyncio.sleep(0)
        yield b'data: {"kind":"done"}\n\n'

    return StreamingResponse(stream_answer(), media_type="text/event-stream")


# ---------- admin endpoints ----------


@app.post("/admin/login")
async def admin_login(payload: AdminLoginRequest, response: Response) -> dict[str, str]:
    return login_admin(payload, response)


@app.post("/admin/logout")
async def admin_logout(response: Response) -> dict[str, str]:
    from backend.audit import ADMIN_COOKIE_NAME

    response.delete_cookie(ADMIN_COOKIE_NAME)
    return {"status": "ok"}


@app.get("/admin/me")
async def admin_me(request: Request) -> dict[str, bool]:
    return {"authenticated": is_admin_request(request)}


@app.get("/admin/stats")
async def admin_stats(request: Request) -> dict[str, object]:
    _require_admin(request)
    settings = get_settings()
    latest_run = store.latest_pipeline_run()
    active_run = store.active_pipeline_run()
    running_usage = (
        store.llm_usage_totals_since(active_run.started_at) if active_run is not None else {}
    )
    return {
        **store.admin_stats(),
        **store.pipeline_cost_estimate(),
        "crawl_max_pages": settings.crawl_max_pages,
        "max_pipeline_cost_usd": settings.max_pipeline_cost_usd,
        "running_llm_dollars": float(running_usage.get("dollars", 0.0)),
        "pipeline_run": _pipeline_run_to_dict(active_run or latest_run),
    }


@app.get("/admin/db")
async def admin_db(request: Request) -> dict[str, object]:
    _require_admin(request)
    return {"tables": store.table_counts()}


@app.post("/admin/reset/extraction")
async def admin_reset_extraction(
    request: Request,
    payload: ResetExtractionRequest,
) -> dict[str, object]:
    _require_admin(request)
    if payload.confirmation != "RESET":
        raise HTTPException(status_code=400, detail='confirmation must be "RESET"')
    return {"status": "ok", "deleted": store.reset_extraction_data()}


@app.post("/admin/pipeline/run/start")
async def admin_pipeline_run_start(request: Request) -> dict[str, object]:
    _require_admin(request)
    active = store.active_pipeline_run()
    if active is not None:
        raise HTTPException(status_code=409, detail="pipeline is already running")
    return _pipeline_run_to_dict(store.create_pipeline_run()) or {}


@app.post("/admin/pipeline/run/{run_id}/finish")
async def admin_pipeline_run_finish(
    run_id: int,
    request: Request,
    payload: PipelineFinishRequest,
) -> dict[str, object]:
    _require_admin(request)
    row = store.finish_pipeline_run(
        run_id,
        status=payload.status,
        error_message=payload.error_message,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="pipeline run not found")
    return _pipeline_run_to_dict(row) or {}


@app.post("/admin/pipeline/run/cancel")
async def admin_pipeline_run_cancel(request: Request) -> dict[str, object]:
    _require_admin(request)
    crawl_status = _stop_job(crawl_job)
    parse_status = _stop_job(parse_job)
    active = store.active_pipeline_run()
    row = None
    if active is not None:
        row = store.finish_pipeline_run(
            active.id,
            status="canceled",
            error_message="Canceled by admin",
        )
    return {
        "status": "canceling" if active is not None else "idle",
        "crawl": crawl_status,
        "parse": parse_status,
        "pipeline_run": _pipeline_run_to_dict(row),
    }


@app.post("/admin/crawl/start")
async def admin_crawl_start(request: Request) -> dict[str, str]:
    _require_admin(request)
    cfg = get_settings()
    seed_urls = list(cfg.crawl_seed_urls or [])
    sitemap_urls = list(cfg.crawl_sitemap_urls or [])
    allowed_domains = list(cfg.crawl_allowed_domains or [])
    max_pages = cfg.crawl_max_pages

    def target(emit: Callable[[ProgressEvent], None]) -> None:
        fetcher = build_fetcher()
        try:
            crawler = SiteCrawler(store=store, fetcher=fetcher)
            asyncio.run(
                crawler.run_sitemap(
                    emit,
                    seed_urls=seed_urls,
                    sitemap_urls=sitemap_urls,
                    allowed_domains=allowed_domains,
                    max_pages=max_pages,
                )
            )
        finally:
            fetcher.close()

    return {"status": _start_job(crawl_job, target)}


@app.post("/admin/crawl/stop")
async def admin_crawl_stop(request: Request) -> dict[str, str]:
    _require_admin(request)
    return {"status": _stop_job(crawl_job)}


@app.get("/admin/crawl/stream")
async def admin_crawl_stream(request: Request) -> StreamingResponse:
    _require_admin(request)
    return StreamingResponse(_event_generator(crawl_job), media_type="text/event-stream")


@app.post("/admin/parse/preview")
async def admin_parse_preview(
    request: Request,
    payload: ParseFilterRequest | None = None,
) -> dict[str, object]:
    _require_admin(request)
    pages = _pages_for_parse_filter(payload or ParseFilterRequest())
    total_estimated_tokens = sum(_estimated_page_tokens(page.page_text) for page in pages)
    estimated_chunks = sum(
        max(1, math.ceil(_estimated_page_tokens(page.page_text) / 4000)) for page in pages
    )
    tier_mode = _extraction_tier_mode()
    concurrency = _parse_concurrency()
    return {
        "page_count": len(pages),
        "total_estimated_tokens": total_estimated_tokens,
        "estimated_dollar_cost": _estimated_parse_dollars(
            total_estimated_tokens,
            len(pages),
            tier_mode=tier_mode,
        ),
        "estimated_wall_clock_seconds": round(
            (estimated_chunks / max(1, concurrency)) * 1.5,
            1,
        ),
        "estimated_chunks": estimated_chunks,
        "tier_mode": tier_mode,
        "parse_concurrency": concurrency,
    }


@app.post("/admin/parse/start")
async def admin_parse_start(
    request: Request,
    payload: ParseFilterRequest | None = None,
    force: bool = False,
) -> dict[str, str | bool]:
    _require_admin(request)
    parse_filter = payload or ParseFilterRequest()

    def target(emit: Callable[[ProgressEvent], None]) -> None:
        parser = build_parser()
        parser.run(
            emit,
            force=force,
            url_pattern=parse_filter.url_pattern,
            keywords=parse_filter.keywords,
            limit=parse_filter.limit,
        )

    return {"status": _start_job(parse_job, target), "force": force}


@app.get("/admin/parse/stream")
async def admin_parse_stream(request: Request) -> StreamingResponse:
    _require_admin(request)
    return StreamingResponse(_event_generator(parse_job), media_type="text/event-stream")


@app.post("/admin/parse/stop")
async def admin_parse_stop(request: Request) -> dict[str, str]:
    _require_admin(request)
    return {"status": _stop_job(parse_job)}


@app.post("/admin/reconcile/run")
async def admin_reconcile_run(request: Request) -> dict[str, int | str]:
    _require_admin(request)
    result = await asyncio.to_thread(reconcile_all, store)
    return result.to_dict()


@app.get("/admin/usage/summary")
async def admin_usage_summary(request: Request) -> dict[str, object]:
    _require_admin(request)
    rows = store.llm_usage_summary(days=30)
    totals = {
        "calls": sum(int(row["calls"]) for row in rows),
        "prompt_tokens": sum(int(row["prompt_tokens"]) for row in rows),
        "completion_tokens": sum(int(row["completion_tokens"]) for row in rows),
        "total_tokens": sum(int(row["total_tokens"]) for row in rows),
        "dollars": sum(float(row["dollars"]) for row in rows),
    }
    return {"days": 30, "totals": totals, "by_day_model": rows}


@app.get("/admin/usage/live")
async def admin_usage_live(request: Request) -> dict[str, object]:
    _require_admin(request)
    return store.llm_usage_live()


@app.post("/admin/audit/run")
async def admin_run_extraction_audit(
    request: Request,
    payload: AuditRunRequest | None = None,
) -> dict[str, object]:
    _require_admin(request)
    settings = get_settings()
    audit_request = payload or AuditRunRequest()
    return await asyncio.to_thread(
        run_extraction_audit,
        store,
        sample_size=audit_request.sample_size,
        use_mock_extract=settings.use_mock_extract,
        openai_api_key=settings.openai_api_key,
    )


@app.get("/admin/audit/last")
async def admin_last_extraction_audit(request: Request) -> dict[str, object]:
    _require_admin(request)
    row = store.latest_audit_run()
    if row is None:
        return {"audit": None}
    return {
        "audit": {
            "id": row.id,
            "run_at": row.run_at.isoformat(),
            "sample_size": row.sample_size,
            "diff_summary": row.diff_summary,
        }
    }


@app.get("/admin/audit")
async def admin_audit(
    request: Request,
    since: datetime | None = None,
    until: datetime | None = None,
    actor: str | None = None,
    action: str | None = None,
    limit: int = 100,
    before_id: int | None = None,
) -> dict[str, object]:
    return audit_events_response(
        store=store,
        request=request,
        since=since,
        until=until,
        actor=actor,
        action=action,
        limit=limit,
        before_id=before_id,
    )


# ---------- static frontends ----------


@app.get("/")
async def frontend_index() -> HTMLResponse:
    return HTMLResponse(Path("frontend/index.html").read_text(encoding="utf-8"))


@app.get("/admin")
async def frontend_admin() -> HTMLResponse:
    return HTMLResponse(Path("frontend/admin.html").read_text(encoding="utf-8"))


@app.get("/app.js")
async def frontend_app_js() -> Response:
    return Response(
        Path("frontend/app.js").read_text(encoding="utf-8"),
        media_type="application/javascript",
    )


@app.get("/admin.js")
async def frontend_admin_js() -> Response:
    return Response(
        Path("frontend/admin.js").read_text(encoding="utf-8"),
        media_type="application/javascript",
    )


@app.get("/styles.css")
async def frontend_styles() -> Response:
    return Response(
        Path("frontend/styles.css").read_text(encoding="utf-8"),
        media_type="text/css",
    )


@app.get("/favicon.svg")
async def frontend_favicon() -> Response:
    return Response(
        Path("frontend/favicon.svg").read_text(encoding="utf-8"),
        media_type="image/svg+xml",
    )
