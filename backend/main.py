from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

from backend.admin_auth import is_admin_request, require_admin
from backend.admin_session import COOKIE_NAME, issue
from backend.config import get_settings
from backend.db.store import Store, source_run_to_dict, source_to_dict
from backend.ingestion.orchestrator import start_run
from backend.normalization.runner import normalize_run
from backend.pipeline.orchestrator import run_full_pipeline, subscribe
from backend.site_auth import SiteAuthMiddleware
from backend.web_api import (
    admin_corpus_stats,
    ask_stream,
    claim_detail,
    delete_source,
    document_detail,
    entity_detail,
    list_conflicts,
    list_directory,
    list_source_documents,
    list_sources,
    reset_extraction,
    resolve_conflict,
    source_breakdown,
    source_detail,
    stats,
    update_source,
    update_source_trust,
    write_feedback,
)


class SourceCreate(BaseModel):
    kind: Literal["domain", "file", "api", "human"]
    identifier: str
    trust_weight: float = Field(default=0.5, ge=0, le=1)
    display_name: str | None = None
    notes: str | None = None


class SourceUpdate(BaseModel):
    display_name: str | None = None
    trust_weight: float | None = Field(default=None, ge=0, le=1)
    status: Literal["active", "paused", "archived"] | None = None
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
SLUG_PATTERN = re.compile(r"[^a-z0-9._-]+")


def _slugify(value: str) -> str:
    return SLUG_PATTERN.sub("-", value.strip().lower()).strip("-") or "source"


def create_app(store: Store | None = None) -> FastAPI:
    app_store = store or Store()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.store.ensure_initial_sources()
        os.makedirs(get_settings().uploads_dir, exist_ok=True)
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

    @app.get("/admin/login")
    async def admin_login_form() -> HTMLResponse:
        return HTMLResponse(_admin_login_html(error=None))

    @app.post("/admin/login", response_model=None)
    async def admin_login_submit(request: Request):
        content_type = request.headers.get("content-type", "")
        password = ""
        next_url = "/"
        if "application/json" in content_type:
            data = await request.json()
            password = str(data.get("password", ""))
            next_url = str(data.get("next", "/"))
        else:
            form = await request.form()
            password = str(form.get("password", ""))
            next_url = str(form.get("next", "/"))
        if not next_url.startswith("/"):
            next_url = "/"
        settings = get_settings()
        expected = settings.pinegraf_admin_password or ""
        if not expected or not secrets.compare_digest(password, expected):
            return HTMLResponse(
                _admin_login_html(error="Wrong password."),
                status_code=401,
            )
        token = issue(user="admin")
        response: RedirectResponse | HTMLResponse
        if "application/json" in content_type:
            response = HTMLResponse('{"ok":true}', media_type="application/json")
        else:
            response = RedirectResponse(url=next_url, status_code=303)
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=settings.admin_session_max_age_seconds,
            httponly=True,
            samesite="lax",
            secure=settings.secure_cookies,
            path="/",
        )
        return response

    @app.post("/admin/logout")
    async def admin_logout() -> RedirectResponse:
        response = RedirectResponse(url="/", status_code=303)
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    @app.get("/api/me")
    async def api_me(request: Request) -> dict[str, object]:
        settings = get_settings()
        return {
            "is_admin": is_admin_request(request),
            "workspace": {
                "slug": settings.workspace_slug,
                "display_name": settings.workspace_display_name,
                "tagline": settings.workspace_tagline,
            },
            "admin_login_url": "/admin/login",
            "admin_logout_url": "/admin/logout",
        }

    @app.get("/api/stats")
    async def api_stats(request: Request) -> dict[str, int]:
        return stats(_store(request))

    @app.get("/api/sources")
    async def api_sources(request: Request) -> dict[str, object]:
        return {"sources": list_sources(_store(request))}

    @app.get("/api/sources/{source_id}")
    async def api_source_detail(request: Request, source_id: uuid.UUID) -> dict[str, object]:
        detail = source_detail(_store(request), source_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="source not found")
        return detail

    @app.get("/api/sources/{source_id}/documents")
    async def api_source_documents(
        request: Request,
        source_id: uuid.UUID,
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, object]:
        if _store(request).get_source(source_id) is None:
            raise HTTPException(status_code=404, detail="source not found")
        return list_source_documents(
            _store(request),
            source_id,
            page=page,
            page_size=page_size,
        )

    @app.get("/api/sources/{source_id}/download")
    async def api_source_download(request: Request, source_id: uuid.UUID) -> FileResponse:
        require_admin(request)
        source = _store(request).get_source(source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="source not found")
        if source.kind != "file":
            raise HTTPException(status_code=400, detail="source is not a file kind")
        path = Path(get_settings().uploads_dir) / source.identifier
        if not path.exists():
            raise HTTPException(status_code=404, detail="upload file no longer on disk")
        return FileResponse(path, filename=source.identifier)

    @app.get("/api/document/{document_id}")
    async def api_document(request: Request, document_id: uuid.UUID) -> dict[str, object]:
        detail = document_detail(_store(request), document_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="document not found")
        return detail

    @app.get("/api/directory")
    async def api_directory(
        request: Request,
        q: str = "",
        org: str = "",
        class_year: str = "",
        source: str = "",
        min_confidence: float = 0.0,
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, object]:
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
    async def api_entity(request: Request, entity_id: uuid.UUID) -> dict[str, object]:
        detail = entity_detail(_store(request), entity_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="entity not found")
        return detail

    @app.get("/api/claim/{claim_id}")
    async def api_claim(request: Request, claim_id: uuid.UUID) -> dict[str, object]:
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

    @app.post("/admin/sources")
    async def admin_create_source(request: Request, payload: SourceCreate) -> dict[str, object]:
        require_admin(request)
        source = _store(request).upsert_source(**payload.model_dump())
        return source_to_dict(source)

    @app.post("/admin/sources/upload")
    async def admin_upload_source(
        request: Request,
        display_name: str = Form(...),
        trust_weight: float = Form(0.9),
        notes: str | None = Form(None),
        file: UploadFile = File(...),
    ) -> dict[str, object]:
        require_admin(request)
        settings = get_settings()
        os.makedirs(settings.uploads_dir, exist_ok=True)
        original_name = file.filename or "upload.bin"
        slug = _slugify(Path(original_name).stem)
        suffix = Path(original_name).suffix
        unique = hashlib.sha256(
            f"{display_name}|{original_name}|{uuid.uuid4()}".encode("utf-8")
        ).hexdigest()[:8]
        stored_name = f"{slug}-{unique}{suffix}"
        path = Path(settings.uploads_dir) / stored_name
        body = await file.read()
        path.write_bytes(body)
        source = _store(request).upsert_source(
            kind="file",
            identifier=stored_name,
            trust_weight=trust_weight,
            display_name=display_name,
            notes=notes,
        )
        return {
            **source_to_dict(source),
            "size_bytes": len(body),
            "original_filename": original_name,
        }

    @app.patch("/admin/sources/{source_id}")
    async def admin_update_source(
        request: Request,
        source_id: uuid.UUID,
        payload: SourceUpdate,
    ) -> dict[str, object]:
        require_admin(request)
        detail = update_source(
            _store(request),
            source_id,
            display_name=payload.display_name,
            trust_weight=payload.trust_weight,
            status=payload.status,
            notes=payload.notes,
        )
        if detail is None:
            raise HTTPException(status_code=404, detail="source not found")
        return detail

    @app.delete("/admin/sources/{source_id}")
    async def admin_delete_source(request: Request, source_id: uuid.UUID) -> dict[str, str]:
        require_admin(request)
        if not delete_source(_store(request), source_id):
            raise HTTPException(status_code=404, detail="source not found")
        return {"status": "archived"}

    @app.post("/admin/sources/{source_id}/crawl")
    async def admin_source_crawl(request: Request, source_id: uuid.UUID) -> dict[str, str]:
        require_admin(request)
        source = _store(request).get_source(source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="source not found")
        if source.kind == "domain":
            run_id = await start_run(
                "sitemap",
                {
                    "source_id": str(source.id),
                    "sitemap_url": f"https://{source.identifier}/sitemap.xml",
                },
                "admin",
                store=_store(request),
            )
        elif source.kind == "file":
            seed_path = Path(get_settings().uploads_dir) / source.identifier
            run_id = await start_run(
                "seed",
                {"source_id": str(source.id), "seed_file_path": str(seed_path)},
                "admin",
                store=_store(request),
            )
        else:
            raise HTTPException(
                status_code=400, detail=f"crawl not supported for kind={source.kind}"
            )
        return {"run_id": str(run_id), "status": "started"}

    @app.post("/admin/sources/{source_id}/parse")
    async def admin_source_parse(request: Request, source_id: uuid.UUID) -> dict[str, str]:
        require_admin(request)
        source = _store(request).get_source(source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="source not found")
        from sqlalchemy import select

        from backend.db.models import SourceRun

        with _store(request).session() as session:
            latest = session.execute(
                select(SourceRun)
                .where(SourceRun.source_id == source_id)
                .order_by(SourceRun.started_at.desc())
                .limit(1)
            ).scalar_one_or_none()
        if latest is None:
            raise HTTPException(status_code=400, detail="no source run to parse — crawl first")
        asyncio.create_task(run_full_pipeline(latest.id, store=_store(request)))
        return {"run_id": str(latest.id), "status": "parsing"}

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
    ) -> dict[str, str]:
        require_admin(request)
        if _store(request).get_source_run(run_id) is None:
            raise HTTPException(status_code=404, detail="run not found")
        asyncio.create_task(run_full_pipeline(run_id, store=_store(request)))
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
        return admin_corpus_stats(_store(request))

    @app.get("/admin/sources/breakdown")
    async def admin_source_breakdown(request: Request) -> dict[str, object]:
        require_admin(request)
        return {"results": source_breakdown(_store(request))}

    @app.get("/admin/conflicts")
    async def admin_conflicts(
        request: Request,
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, object]:
        require_admin(request)
        return list_conflicts(_store(request), page=page, page_size=page_size)

    @app.post("/admin/conflicts/{conflict_id}/resolve")
    async def admin_resolve_conflict(
        request: Request,
        conflict_id: uuid.UUID,
        payload: ConflictResolveRequest,
    ) -> dict[str, str]:
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
    ) -> dict[str, str]:
        require_admin(request)
        update_source_trust(_store(request), source_id, payload.trust_weight)
        return {"status": "ok"}

    @app.post("/admin/reset-extraction")
    async def admin_reset_extraction(
        request: Request,
        payload: ResetExtractionRequest,
    ) -> dict[str, str]:
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


def _admin_login_html(error: str | None) -> str:
    error_block = f'<div class="login-error">{error}</div>' if error else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Admin sign-in — Pinegraf</title>
<link rel="icon" href="/favicon.svg" />
<link rel="stylesheet" href="/styles.css" />
</head>
<body class="login-body">
  <main class="login-shell">
    <div class="login-card">
      <div class="login-brand">
        <svg width="36" height="36" viewBox="0 0 40 40" xmlns="http://www.w3.org/2000/svg">
          <polygon
            points="20,4 12,14 16,14 9,22 14,22 6,32 34,32 26,22 31,22 24,14 28,14"
            fill="#00693E"
          />
          <rect x="18" y="32" width="4" height="4" fill="#00693E"/>
        </svg>
        <div>
          <div class="wordmark">Pinegraf</div>
          <div class="muted small">Admin sign-in</div>
        </div>
      </div>
      {error_block}
      <form method="post" action="/admin/login" class="login-form">
        <label class="field">
          <span>Admin password</span>
          <input name="password" type="password" autofocus required />
        </label>
        <input type="hidden" name="next" value="/" />
        <button type="submit" class="btn-primary">Sign in</button>
      </form>
      <p class="muted small">
        This is a separate password from the site-wide sign-in.
      </p>
    </div>
  </main>
</body>
</html>
"""


app = create_app()
