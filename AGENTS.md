# Pinegraf

Pipeline: take a seed list of alumni -> crawl public pages -> parse stored pages into source-linked structured data -> answer natural-language questions from the DB or raw-page RAG.

## Stack

- Python 3.11+, FastAPI, SQLAlchemy, Postgres 14+ for production, SQLite fallback for dev, pytest
- OpenAI Python SDK; `gpt-5.4-mini` for extraction and validation, `gpt-5.4` for synthesis, `gpt-5.3-mini` for strict query, `gpt-5.5` for deep query
- Vanilla HTML/JS frontend served by FastAPI

## Architecture

- Stage 1 crawl: `backend/pipeline/crawler.py`, `page_fetcher.py`, `search.py`
- Stage 2 parse: `backend/pipeline/parser.py`
- Stage 3 query: `backend/pipeline/query.py`
- Database CRUD and full-text helper: `backend/db/store.py`
- ORM schema: `backend/db/models.py`
- FastAPI endpoints and stage job streaming: `backend/main.py`

## Conventions

- All secrets via `.env` and `python-dotenv`. Never hardcode keys.
- Every external API call must have a mockable interface; tests must not hit real APIs.
- Pipeline stages should remain independently runnable and side effects should stay isolated to `db/store.py` where practical.
- Type hints everywhere. Pydantic models for LLM I/O.
- Format with `ruff format`. Lint with `ruff check`.

## Commands

- Install: `pip install -e .`
- Start local Postgres: `docker compose up -d postgres`
- Run dev server: `uvicorn backend.main:app --reload`
- Test: `pytest -v`

## Things To Avoid

- Don't add Next.js, React, or any heavy frontend framework. Plain HTML/JS only.
- Don't write code that scrapes LinkedIn directly. Use search results and fetch only public non-LinkedIn pages.
- Don't commit `.env`, `*.db`, or `__pycache__`.
# Append the section below to the existing AGENTS.md.
# Keep everything that's already in AGENTS.md; just add this at the end.

## Multi-agent workflow

This repo has specialist subagents in `.codex/agents/`. The main Codex thread
is the orchestrator and should delegate rather than do everything itself.

### Ownership map (strict — reviewer enforces)

| Path | Owner |
|---|---|
| `backend/**`, `tests/**` | `backend-engineer` |
| `frontend/**` | `frontend-engineer` |
| `AGENTS.md`, `README.md`, `pyproject.toml`, `docker-compose.yml`, `scripts/`, `data/` | orchestrator (main thread) |

### When to spawn subagents

**Fan-out in parallel** when a feature crosses backend AND frontend:
1. Main thread sketches the API contract (endpoint path, request shape,
   response shape) and writes it down in the spawn instructions.
2. Spawn `backend-engineer` and `frontend-engineer` in parallel with that
   contract.
3. After both finish, spawn `reviewer` on the full diff.

**Single agent** when the change is fully inside one zone:
- "Add a cache layer to the SerpAPI client" → just `backend-engineer`
- "Show enrichment progress per-row in the UI" → just `frontend-engineer`

**No subagents** for:
- Single-file edits, one-line fixes, typo fixes
- Pure exploration ("what does crawler.py do?")
- Changes to repo root files (README, pyproject.toml, scripts/, data/)
- Anything you'd finish faster than the spawn overhead (~30s cold)

### Conflict prevention

The ownership map prevents most conflicts. Two remaining risks:

1. **Static mount paths in `backend/main.py` reference `frontend/`.** If
   frontend-engineer renames a file, backend-engineer needs to update the
   mount. Surface this in the orchestrator plan when it happens.

2. **API contract drift.** If backend-engineer changes a response shape and
   frontend-engineer is mid-flight against the old shape, you'll see it in
   the reviewer's "contract mismatches" section. Re-spawn frontend with the
   updated contract.
# Append the section below to the existing AGENTS.md.
# Keep everything that's already in AGENTS.md; just add this at the end.

## Multi-agent workflow

This repo has specialist subagents in `.codex/agents/`. The main Codex thread
is the orchestrator and should delegate rather than do everything itself.

### Ownership map (strict — reviewer enforces)

| Path | Owner |
|---|---|
| `backend/**`, `tests/**` | `backend-engineer` |
| `frontend/**` | `frontend-engineer` |
| `AGENTS.md`, `README.md`, `pyproject.toml`, `docker-compose.yml`, `scripts/`, `data/` | orchestrator (main thread) |

### When to spawn subagents

**Fan-out in parallel** when a feature crosses backend AND frontend:
1. Main thread sketches the API contract (endpoint path, request shape,
   response shape) and writes it down in the spawn instructions.
2. Spawn `backend-engineer` and `frontend-engineer` in parallel with that
   contract.
3. After both finish, spawn `reviewer` on the full diff.

**Single agent** when the change is fully inside one zone:
- "Add a cache layer to the SerpAPI client" → just `backend-engineer`
- "Show enrichment progress per-row in the UI" → just `frontend-engineer`

**No subagents** for:
- Single-file edits, one-line fixes, typo fixes
- Pure exploration ("what does crawler.py do?")
- Changes to repo root files (README, pyproject.toml, scripts/, data/)
- Anything you'd finish faster than the spawn overhead (~30s cold)

### Conflict prevention

The ownership map prevents most conflicts. Two remaining risks:

1. **Static mount paths in `backend/main.py` reference `frontend/`.** If
   frontend-engineer renames a file, backend-engineer needs to update the
   mount. Surface this in the orchestrator plan when it happens.

2. **API contract drift.** If backend-engineer changes a response shape and
   frontend-engineer is mid-flight against the old shape, you'll see it in
   the reviewer's "contract mismatches" section. Re-spawn frontend with the
   updated contract.
