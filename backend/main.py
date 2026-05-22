from __future__ import annotations
import os

import asyncio
import csv
import json
import logging
import queue
import threading
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
import secrets
from fastapi import Cookie, HTTPException
import secrets
from fastapi import Cookie, HTTPException
import secrets
from fastapi import Cookie, HTTPException
from fastapi.responses import HTMLResponse, Response, StreamingResponse
import secrets
from fastapi import Cookie, HTTPException
import secrets
from fastapi import Cookie, HTTPException
import secrets
from fastapi import Cookie, HTTPException
from pydantic import BaseModel, Field


import secrets
from fastapi import Cookie, HTTPException
import secrets
from fastapi import Cookie, HTTPException
import secrets
from fastapi import Cookie, HTTPException
import secrets
from fastapi import Cookie, HTTPException

from backend.audit import (
    AdminLoginRequest,
    audit_events_response,
    install_audit_middleware,
    login_admin,
)
from backend.config import get_settings
from backend.db.store import KEEP_VERDICTS, SQLITE_WARNING, Store
from backend.pipeline.crawler import ProgressEvent, SiteCrawler
from backend.pipeline.page_fetcher import MockPageFetcher, PageFetcher
from backend.pipeline.parser import (
    MockExtractionClient,
    MockSynthesisClient,
    MockValidationClient,
    OpenAIExtractionClient,
    OpenAISynthesisClient,
    OpenAIValidationClient,
    Parser,
)
from backend.pipeline.query import (
    DeepQueryClient,
    MockDeepQueryClient,
    MockQueryClient,
    OpenAIQueryClient,
    QueryClient,
)

logger = logging.getLogger(__name__)
DONE_SENTINEL = "__done__"


class QueryRequest(BaseModel):
    question: str
    mode: Literal["strict", "deep"] = Field(default="strict")


@dataclass
class StageJob:
    name: str
    queue: queue.Queue[ProgressEvent | str] = field(default_factory=queue.Queue)
    running: bool = False
    thread: threading.Thread | None = None


def load_alumni_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        records: list[dict[str, str]] = []
        for row in reader:
            name = row["name"].strip()
            class_year = row["class_year"].strip()
            if not name or name.lower() == "name":
                continue
            records.append({"name": name, "class_year": class_year})
        return records


def build_fetcher() -> PageFetcher:
    settings = get_settings()
    if settings.use_mock_fetch:
        return MockPageFetcher()
    return PageFetcher()


def build_parser() -> Parser:
    settings = get_settings()
    if settings.use_mock_extract:
        extractor = MockExtractionClient()
        validator = MockValidationClient()
        synthesizer = MockSynthesisClient()
    else:
        extractor = OpenAIExtractionClient(api_key=settings.openai_api_key, model="gpt-5.4-mini")
        validator = OpenAIValidationClient(api_key=settings.openai_api_key, model="gpt-5.4-mini")
        synthesizer = OpenAISynthesisClient(api_key=settings.openai_api_key, model="gpt-5.4")
    return Parser(
        store=store,
        extractor=extractor,
        validator=validator,
        synthesizer=synthesizer,
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


app = FastAPI(title="Pinegraf")

class LoginRequest(BaseModel):
    password: str

class LookupRequest(BaseModel):
    name: str | None = None
    company: str | None = None
    class_year: str | None = None

class ResearchRequest(BaseModel):
    question: str
    mode: Literal["strict", "deep"] = Field(default="deep")

settings = get_settings()
store = Store(settings.database_url)
store.init_db()
if store.is_sqlite:
    logger.warning(SQLITE_WARNING)
install_audit_middleware(app, store)

crawl_job = StageJob("crawl")
parse_job = StageJob("parse")


def _reset_job(job: StageJob) -> None:
    job.queue = queue.Queue()
    job.running = False
    job.thread = None


def _start_job(job: StageJob, target: Callable[[Callable[[ProgressEvent], None]], None]) -> str:
    if job.running:
        return "already_running"
    _reset_job(job)
    job.running = True

    def emit(event: ProgressEvent) -> None:
        job.queue.put(event)

    def worker() -> None:
        try:
            target(emit)
        except Exception as exc:
            job.queue.put(ProgressEvent("done", {"error": f"{type(exc).__name__}: {exc}"}))
        finally:
            job.running = False
            job.queue.put(DONE_SENTINEL)

    job.thread = threading.Thread(target=worker, daemon=True, name=f"pinegraf-{job.name}")
    job.thread.start()
    return "started"


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


@app.post("/crawl/start")
async def crawl_start() -> dict[str, str]:
    settings = get_settings()
    seed_urls = list(getattr(settings, "crawl_seed_urls", []) or [])
    sitemap_urls = list(getattr(settings, "crawl_sitemap_urls", []) or [])
    allowed_domains = list(getattr(settings, "crawl_allowed_domains", []) or [])
    max_pages = getattr(settings, "crawl_max_pages", 500)

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


@app.get("/crawl/stream")
async def crawl_stream() -> StreamingResponse:
    return StreamingResponse(_event_generator(crawl_job), media_type="text/event-stream")


@app.post("/parse/start")
async def parse_start(force: bool = False) -> dict[str, str | bool]:
    def target(emit: Callable[[ProgressEvent], None]) -> None:
        parser = build_parser()
        parser.run(emit, force=force)

    return {"status": _start_job(parse_job, target), "force": force}


@app.get("/parse/stream")
async def parse_stream() -> StreamingResponse:
    return StreamingResponse(_event_generator(parse_job), media_type="text/event-stream")


@app.get("/alumni-count")
async def alumni_count() -> dict[str, int]:
    alumni = load_alumni_csv(Path("data/alumni.csv"))
    return {"count": len(alumni)}


@app.get("/profiles")
async def list_profiles() -> dict[str, object]:
    return {
        "profiles": [
            {
                "name": profile.name,
                "entity_id": str(profile.entity_id) if profile.entity_id else None,
                "class_year": profile.class_year,
                "current_company": profile.current_company,
                "current_title": profile.current_title,
                "past_companies": profile.past_companies,
                "education": profile.education,
                "bio_summary": profile.bio_summary,
                "discovered_via": profile.discovered_via,
                "last_parsed_at": (
                    profile.last_parsed_at.isoformat() if profile.last_parsed_at else None
                ),
            }
            for profile in store.list_profiles()
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


@app.post("/query")
async def query(payload: QueryRequest) -> dict[str, str]:
    answer = build_query_client(payload.mode).answer_question(payload.question)
    return {"answer": answer.answer, "mode": payload.mode}


@app.post("/lookup")
async def lookup(payload: LookupRequest) -> dict[str, object]:
    profiles = store.list_profiles()
    name_q = (payload.name or "").strip().lower()
    company_q = (payload.company or "").strip().lower()
    year_q = (payload.class_year or "").strip().lower()

    def matches(p) -> bool:
        if name_q and name_q not in (p.name or "").lower():
            return False
        if company_q:
            haystack = " ".join([(p.current_company or ""), " ".join(p.past_companies or [])]).lower()
            if company_q not in haystack:
                return False
        if year_q and year_q not in (p.class_year or "").lower():
            return False
        return True

    matched = [p for p in profiles if matches(p)]
    return {
        "count": len(matched),
        "results": [
            {
                "name": p.name,
                "class_year": p.class_year,
                "current_company": p.current_company,
                "current_title": p.current_title,
                "past_companies": p.past_companies,
                "education": p.education,
                "bio_summary": p.bio_summary,
            }
            for p in matched
        ],
    }




@app.post("/admin/logout")
async def admin_logout(response: Response, request: Request) -> dict[str, str]:
    response.delete_cookie(ADMIN_COOKIE_NAME)
    return {"status": "ok"}


@app.get("/admin/me")
async def admin_me(request: Request) -> dict[str, bool]:
    return {"authenticated": _is_admin(request)}


@app.post("/admin/crawl/start")
async def admin_crawl_start(request: Request) -> dict[str, str]:
    _require_admin(request)
    return await crawl_start()


@app.get("/admin/crawl/stream")
async def admin_crawl_stream(request: Request) -> StreamingResponse:
    _require_admin(request)
    return StreamingResponse(_event_generator(crawl_job), media_type="text/event-stream")


@app.post("/admin/parse/start")
async def admin_parse_start(request: Request, force: bool = False) -> dict[str, str | bool]:
    _require_admin(request)
    return await parse_start(force=force)


@app.get("/admin/parse/stream")
async def admin_parse_stream(request: Request) -> StreamingResponse:
    _require_admin(request)
    return StreamingResponse(_event_generator(parse_job), media_type="text/event-stream")


@app.get("/admin/alumni-count")
async def admin_alumni_count(request: Request) -> dict[str, int]:
    _require_admin(request)
    return await alumni_count()


@app.post("/research")
async def research(payload: ResearchRequest) -> dict[str, str]:
    answer = build_query_client(payload.mode).answer_question(payload.question)
    return {"answer": answer.answer, "mode": payload.mode}


@app.get("/admin")
async def frontend_admin() -> HTMLResponse:
    return HTMLResponse(Path("frontend/admin.html").read_text(encoding="utf-8"))


@app.get("/admin.js")
async def frontend_admin_js() -> Response:
    return Response(Path("frontend/admin.js").read_text(encoding="utf-8"), media_type="application/javascript")


# ---------- UI split additions ----------

ADMIN_COOKIE_NAME = "pinegraf_admin"
_admin_tokens: set[str] = set()








def _is_admin(request: object) -> bool:
    from backend.audit import is_admin_request
    return is_admin_request(request)


def _require_admin(request: object) -> None:
    if not _is_admin(request):
        raise HTTPException(status_code=401, detail='admin auth required')






from backend.audit import AdminLoginRequest, login_admin as _audit_login_admin


@app.post("/admin/login")
async def admin_login(payload: AdminLoginRequest, response: Response) -> dict[str, str]:
    return _audit_login_admin(payload, response)


@app.get("/")
async def frontend_index() -> HTMLResponse:
    return HTMLResponse(Path("frontend/index.html").read_text(encoding="utf-8"))


@app.get("/app.js")
async def frontend_app_js() -> Response:
    return Response(Path("frontend/app.js").read_text(encoding="utf-8"), media_type="application/javascript")
