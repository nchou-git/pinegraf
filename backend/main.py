from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from backend.admin_auth import is_admin_request, require_admin
from backend.db.store import Store, source_run_to_dict, source_to_dict
from backend.ingestion.orchestrator import start_run
from backend.normalization.runner import normalize_run
from backend.pipeline.orchestrator import run_full_pipeline, subscribe
from backend.site_auth import SiteAuthMiddleware
from backend.web_api import (
    ask_stream,
    claim_detail,
    entity_detail,
    list_conflicts,
    list_directory,
    list_sources,
    reset_extraction,
    resolve_conflict,
    stats,
    update_source_trust,
    write_feedback,
)


class SourceCreate(BaseModel):
    kind: Literal["domain", "file", "api", "human"]
    identifier: str
    trust_weight: float = Field(default=0.5, ge=0, le=1)
    display_name: str | None = None
    notes: str | None = None


class SitemapRunCreate(BaseModel):
    source_id: uuid.UUID
    sitemap_url: str


class SeedRunCreate(BaseModel):
    source_id: uuid.UUID
    seed_file_path: str


class AdhocRunCreate(BaseModel):
    source_id: uuid.UUID
    urls: list[str]


class AskRequest(BaseModel):
    question: str
    max_results: int = Field(default=10, ge=1, le=50)


class FeedbackRequest(BaseModel):
    target_type: Literal["claim", "entity", "mention", "evidence"]
    target_id: uuid.UUID
    signal_type: Literal[
        "verify",
        "dispute",
        "correct",
        "add_evidence",
        "redact",
        "merge_entities",
        "split_entity",
        "retract_claim",
    ]
    payload: dict[str, object] | None = None


class ConflictResolveRequest(BaseModel):
    resolution: Literal[
        "unresolved",
        "claim_a_wins",
        "claim_b_wins",
        "both_valid_temporal",
        "both_valid_distinct",
    ]
    notes: str | None = None


class SourceTrustRequest(BaseModel):
    trust_weight: float = Field(ge=0, le=1)


class ResetExtractionRequest(BaseModel):
    confirm: str


FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


def create_app(store: Store | None = None) -> FastAPI:
    app_store = store or Store()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.store.ensure_initial_sources()
        yield

    app = FastAPI(title="Pinegraf", lifespan=lifespan)
    app.state.store = app_store
    app.add_middleware(SiteAuthMiddleware)

    @app.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "index.html")

    @app.get("/app.js")
    async def app_js() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "app.js")

    @app.get("/styles.css")
    async def styles_css() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "styles.css")

    @app.get("/favicon.svg")
    async def favicon() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "favicon.svg")

    @app.get("/api/me")
    async def api_me(request: Request, workspace: str = "tuck") -> dict[str, object]:
        return {
            "is_admin": is_admin_request(request),
            "workspace": {"slug": workspace, "display_name": "Tuck alumni"},
        }

    @app.get("/api/stats")
    async def api_stats(request: Request) -> dict[str, int]:
        return stats(_store(request))

    @app.get("/api/sources")
    async def api_sources(request: Request) -> dict[str, object]:
        return {"sources": list_sources(_store(request))}

    @app.get("/api/directory")
    async def api_directory(
        request: Request,
        q: str = "",
        org: str = "",
        class_year: str = "",
        source: str = "",
        min_confidence: float = 0.6,
        page: int = 1,
        page_size: int = 25,
        workspace: str = "tuck",
    ) -> dict[str, object]:
        del workspace
        return list_directory(
            _store(request),
            q=q,
            org=org,
            class_year=class_year,
            source=source,
            min_confidence=min_confidence,
            page=page,
            page_size=page_size,
        )

    @app.get("/api/entity/{entity_id}")
    async def api_entity(
        request: Request,
        entity_id: uuid.UUID,
        workspace: str = "tuck",
    ) -> dict[str, object]:
        del workspace
        detail = entity_detail(_store(request), entity_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="entity not found")
        return detail

    @app.get("/api/claim/{claim_id}")
    async def api_claim(
        request: Request,
        claim_id: uuid.UUID,
        workspace: str = "tuck",
    ) -> dict[str, object]:
        del workspace
        detail = claim_detail(_store(request), claim_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="claim not found")
        return detail

    @app.post("/api/ask")
    async def api_ask(request: Request, payload: AskRequest) -> StreamingResponse:
        return StreamingResponse(
            ask_stream(_store(request), question=payload.question, max_results=payload.max_results),
            media_type="text/event-stream",
        )

    @app.post("/api/feedback")
    async def api_feedback(request: Request, payload: FeedbackRequest) -> dict[str, str]:
        signal_id = write_feedback(
            _store(request),
            target_type=payload.target_type,
            target_id=payload.target_id,
            signal_type=payload.signal_type,
            payload=payload.payload,
        )
        return {"signal_id": str(signal_id)}

    @app.get("/admin")
    async def admin_index(request: Request) -> FileResponse:
        require_admin(request)
        return FileResponse(FRONTEND_DIR / "index.html")

    @app.post("/admin/sources")
    async def admin_create_source(request: Request, payload: SourceCreate) -> dict[str, object]:
        require_admin(request)
        source = _store(request).upsert_source(**payload.model_dump())
        return source_to_dict(source)

    @app.post("/admin/runs/sitemap")
    async def admin_run_sitemap(request: Request, payload: SitemapRunCreate) -> dict[str, str]:
        require_admin(request)
        _ensure_source(_store(request), payload.source_id)
        run_id = await start_run(
            "sitemap",
            {"source_id": str(payload.source_id), "sitemap_url": payload.sitemap_url},
            "admin",
            store=_store(request),
        )
        return {"run_id": str(run_id)}

    @app.post("/admin/runs/seed")
    async def admin_run_seed(request: Request, payload: SeedRunCreate) -> dict[str, str]:
        require_admin(request)
        _ensure_source(_store(request), payload.source_id)
        run_id = await start_run(
            "seed",
            {"source_id": str(payload.source_id), "seed_file_path": payload.seed_file_path},
            "admin",
            store=_store(request),
        )
        return {"run_id": str(run_id)}

    @app.post("/admin/runs/adhoc")
    async def admin_run_adhoc(request: Request, payload: AdhocRunCreate) -> dict[str, str]:
        require_admin(request)
        _ensure_source(_store(request), payload.source_id)
        run_id = await start_run(
            "adhoc",
            {"source_id": str(payload.source_id), "urls": payload.urls},
            "admin",
            store=_store(request),
        )
        return {"run_id": str(run_id)}

    @app.post("/admin/runs/{run_id}/normalize")
    async def admin_normalize_run(request: Request, run_id: uuid.UUID) -> dict[str, object]:
        require_admin(request)
        if _store(request).get_source_run(run_id) is None:
            raise HTTPException(status_code=404, detail="run not found")
        document_ids = await normalize_run(run_id, store=_store(request))
        return {"run_id": str(run_id), "document_ids": [str(value) for value in document_ids]}

    @app.post("/admin/runs/{run_id}/pipeline")
    async def admin_run_pipeline(
        request: Request,
        run_id: uuid.UUID,
        workspace: str = "tuck",
    ) -> dict[str, str]:
        require_admin(request)
        if _store(request).get_source_run(run_id) is None:
            raise HTTPException(status_code=404, detail="run not found")
        asyncio.create_task(run_full_pipeline(workspace, run_id, store=_store(request)))
        return {"run_id": str(run_id), "status": "started"}

    @app.get("/admin/runs/{run_id}/stream")
    async def admin_run_stream(request: Request, run_id: uuid.UUID) -> StreamingResponse:
        require_admin(request)

        async def events() -> AsyncIterator[bytes]:
            async for event in subscribe(run_id):
                payload = {
                    "stage": event.stage,
                    "status": event.status,
                    "message": event.message,
                    "percent": event.percent,
                    "data": event.data,
                }
                yield f"data: {json.dumps(payload, default=str)}\n\n".encode("utf-8")

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.get("/admin/runs/{run_id}")
    async def admin_get_run(request: Request, run_id: uuid.UUID) -> dict[str, object]:
        require_admin(request)
        row = source_run_to_dict(_store(request).get_source_run(run_id))
        if row is None:
            raise HTTPException(status_code=404, detail="run not found")
        return row

    @app.get("/admin/stats")
    async def admin_stats(request: Request) -> dict[str, int]:
        require_admin(request)
        return _store(request).table_counts()

    @app.get("/admin/conflicts")
    async def admin_conflicts(
        request: Request,
        page: int = 1,
        page_size: int = 25,
        workspace: str = "tuck",
    ) -> dict[str, object]:
        del workspace
        require_admin(request)
        return list_conflicts(_store(request), page=page, page_size=page_size)

    @app.post("/admin/conflicts/{conflict_id}/resolve")
    async def admin_resolve_conflict(
        request: Request,
        conflict_id: uuid.UUID,
        payload: ConflictResolveRequest,
        workspace: str = "tuck",
    ) -> dict[str, str]:
        del workspace
        require_admin(request)
        resolve_conflict(
            _store(request),
            conflict_id=conflict_id,
            resolution=payload.resolution,
            notes=payload.notes,
        )
        return {"status": "ok"}

    @app.post("/admin/sources/{source_id}/trust")
    async def admin_source_trust(
        request: Request,
        source_id: uuid.UUID,
        payload: SourceTrustRequest,
        workspace: str = "tuck",
    ) -> dict[str, str]:
        del workspace
        require_admin(request)
        update_source_trust(_store(request), source_id, payload.trust_weight)
        return {"status": "ok"}

    @app.post("/admin/reset-extraction")
    async def admin_reset_extraction(
        request: Request,
        payload: ResetExtractionRequest,
        workspace: str = "tuck",
    ) -> dict[str, str]:
        del workspace
        require_admin(request)
        if payload.confirm != "RESET":
            raise HTTPException(status_code=400, detail='confirm must be "RESET"')
        reset_extraction(_store(request))
        return {"status": "ok"}

    return app


def _store(request: Request) -> Store:
    return request.app.state.store


def _ensure_source(store: Store, source_id: uuid.UUID) -> None:
    if store.get_source(source_id) is None:
        raise HTTPException(status_code=404, detail="source not found")


app = create_app()
