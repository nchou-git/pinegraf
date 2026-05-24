from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from backend.admin_auth import require_admin
from backend.db.store import Store, source_run_to_dict, source_to_dict
from backend.ingestion.orchestrator import start_run
from backend.normalization.runner import normalize_run
from backend.site_auth import SiteAuthMiddleware


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

    return app


def _store(request: Request) -> Store:
    return request.app.state.store


def _ensure_source(store: Store, source_id: uuid.UUID) -> None:
    if store.get_source(source_id) is None:
        raise HTTPException(status_code=404, detail="source not found")


app = create_app()
