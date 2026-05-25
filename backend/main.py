from __future__ import annotations

import asyncio
import hashlib
import os
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

from backend.admin_auth import (
    ADMIN_USERNAME,
    is_admin_request,
    require_admin,
    valid_admin_credentials,
)
from backend.admin_session import COOKIE_NAME, issue
from backend.config import get_settings
from backend.db.store import Store, source_to_dict
from backend.ingestion.orchestrator import start_run
from backend.pipeline.orchestrator import run_full_pipeline
from backend.web_api import (
    archived_source_count,
    ask_stream,
    delete_source,
    document_detail,
    entity_detail,
    list_conflicts,
    list_directory,
    list_source_documents,
    list_sources,
    resolve_conflict,
    source_detail,
    stats,
    update_source,
)


class SourceCreate(BaseModel):
    kind: Literal["domain", "file"]
    identifier: str
    trust_weight: float = Field(default=0.5, ge=0, le=1)
    display_name: str | None = None
    notes: str | None = None


class SourceUpdate(BaseModel):
    display_name: str | None = None
    trust_weight: float | None = Field(default=None, ge=0, le=1)
    status: Literal["active", "paused", "archived"] | None = None
    notes: str | None = None


class AskRequest(BaseModel):
    question: str
    max_results: int = Field(default=10, ge=1, le=50)


class ConflictResolveRequest(BaseModel):
    resolution: Literal[
        "unresolved",
        "claim_a_wins",
        "claim_b_wins",
        "both_valid_temporal",
        "both_valid_distinct",
    ]
    notes: str | None = None


FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
SLUG_PATTERN = re.compile(r"[^a-z0-9._-]+")
FILE_UPLOAD_EXTENSIONS = {".xlsx", ".csv", ".json", ".tsv", ".txt", ".md", ".pdf", ".html"}


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

    @app.get("/styles/{filename}")
    async def nested_style(filename: str) -> FileResponse:
        path = FRONTEND_DIR / "styles" / filename
        if path.parent != FRONTEND_DIR / "styles" or not path.is_file():
            raise HTTPException(status_code=404, detail="style not found")
        return FileResponse(path)

    @app.get("/favicon.svg")
    async def favicon() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "favicon.svg")

    @app.get("/admin/login")
    async def admin_login_form() -> HTMLResponse:
        return HTMLResponse(_admin_login_html(error=None))

    @app.post("/admin/login", response_model=None)
    async def admin_login_submit(request: Request):
        content_type = request.headers.get("content-type", "")
        username = ""
        password = ""
        next_url = "/"
        if "application/json" in content_type:
            data = await request.json()
            username = str(data.get("username", ""))
            password = str(data.get("password", ""))
            next_url = str(data.get("next", "/"))
        else:
            form = await request.form()
            username = str(form.get("username", ""))
            password = str(form.get("password", ""))
            next_url = str(form.get("next", "/"))
        if not next_url.startswith("/"):
            next_url = "/"
        if not valid_admin_credentials(username, password):
            return HTMLResponse(
                _admin_login_html(error="Wrong username or password."),
                status_code=401,
            )
        settings = get_settings()
        token = issue(user=ADMIN_USERNAME)
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
            },
            "admin_login_url": "/admin/login",
            "admin_logout_url": "/admin/logout",
        }

    @app.get("/api/stats")
    async def api_stats(request: Request) -> dict[str, int]:
        return stats(_store(request))

    @app.get("/api/sources")
    async def api_sources(request: Request) -> dict[str, object]:
        return {
            "sources": list_sources(_store(request)),
            "archived_count": archived_source_count(_store(request)),
        }

    @app.get("/api/sources/archived")
    async def api_archived_sources(request: Request) -> dict[str, object]:
        sources = [
            source
            for source in list_sources(_store(request), include_archived=True)
            if source["status"] == "archived"
        ]
        return {"sources": sources, "archived_count": len(sources)}

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

    @app.post("/api/ask")
    async def api_ask(request: Request, payload: AskRequest) -> StreamingResponse:
        return StreamingResponse(
            ask_stream(_store(request), question=payload.question, max_results=payload.max_results),
            media_type="text/event-stream",
        )

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

    @app.post("/admin/sources/{source_id}/upload")
    async def admin_replace_source_upload(
        request: Request,
        source_id: uuid.UUID,
        file: UploadFile = File(...),
    ) -> dict[str, object]:
        require_admin(request)
        store = _store(request)
        source = store.get_source(source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="source not found")
        if source.kind != "file":
            raise HTTPException(status_code=400, detail="source is not a file kind")

        settings = get_settings()
        os.makedirs(settings.uploads_dir, exist_ok=True)
        original_name = file.filename or "upload.bin"
        suffix = Path(original_name).suffix
        if suffix.lower() not in FILE_UPLOAD_EXTENSIONS:
            raise HTTPException(status_code=400, detail="file type not supported")

        slug = _slugify(Path(original_name).stem)
        unique = hashlib.sha256(
            f"{source.display_name or source.identifier}|{original_name}|{uuid.uuid4()}".encode(
                "utf-8"
            )
        ).hexdigest()[:8]
        stored_name = f"{slug}-{unique}{suffix}"
        path = Path(settings.uploads_dir) / stored_name
        body = await file.read()
        path.write_bytes(body)

        from backend.db.models import Source

        old_path = Path(settings.uploads_dir) / source.identifier
        with store.session() as session:
            db_source = session.get(Source, source_id)
            if db_source is None:
                raise HTTPException(status_code=404, detail="source not found")
            db_source.identifier = stored_name
            session.commit()
        if old_path != path and old_path.exists():
            try:
                old_path.unlink()
            except OSError:
                pass
        detail = source_detail(store, source_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="source not found")
        return detail

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
        return {"status": "deleted"}

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

    return app


def _store(request: Request) -> Store:
    return request.app.state.store


def _admin_login_html(error: str | None) -> str:
    error_block = f'<div class="login-error">{error}</div>' if error else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Pinegraf</title>
<link rel="icon" href="/favicon.svg" />
<link rel="stylesheet" href="/styles.css" />
</head>
<body class="login-body">
  <main class="login-shell">
    <div class="login-card">
      <div class="login-brand">
        <svg
          class="login-mark"
          viewBox="0 0 40 40"
          xmlns="http://www.w3.org/2000/svg"
          aria-hidden="true"
        >
          <polygon
            points="20,4 12,14 16,14 9,22 14,22 6,32 34,32 26,22 31,22 24,14 28,14"
            fill="currentColor"
          />
          <rect x="18" y="32" width="4" height="4" fill="currentColor"/>
        </svg>
        <div>
          <div class="wordmark">Pinegraf</div>
        </div>
      </div>
      {error_block}
      <form method="post" action="/admin/login" class="login-form">
        <label class="field">
          <span class="field-label">Login ID</span>
          <input
            class="input"
            name="username"
            autocomplete="username"
            value="pinegraf"
            autofocus
            required
          />
        </label>
        <label class="field">
          <span class="field-label">Password</span>
          <span class="password-field">
            <input
              class="input password-input"
              id="admin-password"
              name="password"
              type="password"
              autocomplete="current-password"
              required
            />
            <button
              class="btn-icon-only password-toggle"
              type="button"
              aria-label="Show password"
              aria-pressed="false"
              aria-controls="admin-password"
              onclick="togglePasswordVisibility(this)"
            >
              <svg
                class="password-eye"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
                aria-hidden="true"
              >
                <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z" />
                <circle cx="12" cy="12" r="3" />
                <path class="password-eye-slash" d="M3 3l18 18" style="display:none" />
              </svg>
            </button>
          </span>
        </label>
        <input type="hidden" name="next" value="/" />
        <button type="submit" class="btn-primary">Sign in</button>
      </form>
      <div class="login-note">Admin access controls source management.</div>
    </div>
  </main>
  <script>
    function togglePasswordVisibility(button) {{
      const input = document.getElementById("admin-password");
      const show = input.type === "password";
      const slash = button.querySelector(".password-eye-slash");
      input.type = show ? "text" : "password";
      slash.style.display = show ? "" : "none";
      button.setAttribute("aria-label", show ? "Hide password" : "Show password");
      button.setAttribute("aria-pressed", show ? "true" : "false");
      input.focus();
    }}
  </script>
</body>
</html>
"""


app = create_app()
