# Pinegraf

Pipeline: seed alumni -> crawl public pages -> snapshot sources -> parse into
source-linked structured data -> resolve entities -> answer analyst questions
from structured rows or raw-page RAG.

## Stack

- Python 3.11+, FastAPI, SQLAlchemy 2.x, Alembic, Postgres 14+ for production,
  SQLite for local tests, pytest.
- OpenAI Python SDK; use mock clients in tests.
- `httpx` async crawler with `h2`, `selectolax` HTML parsing, plain HTML/JS
  frontend served by FastAPI.

## Architecture

- Crawl: `backend/pipeline/crawler.py`, `backend/pipeline/page_fetcher.py`
- Parse: `backend/pipeline/parser.py`
- Resolve: `backend/resolution/entity_resolver.py`
- Query: `backend/pipeline/query.py`
- Persistence: `backend/db/store.py`
- ORM schema: `backend/db/models.py`
- FastAPI route definitions: `backend/main.py`
- Audit middleware and admin helpers: `backend/audit.py`

## Conventions

- Type hints everywhere. Pydantic models for LLM I/O.
- `backend/main.py` contains route definitions only; put logic in modules.
- Run `ruff check .`, `ruff format .`, and applicable `pytest -v` tests before
  every commit.
- All secrets and deployment-specific values come from `.env` via
  `python-dotenv`; never hardcode keys.
- Every external API call must have a mockable interface; tests must not hit
  real APIs.
- Pipeline stages should remain independently runnable. Keep database side
  effects isolated to `backend/db/store.py` where practical.
- Use timezone-aware UTC datetimes (`datetime.now(UTC)`).
- New intelligence-pipeline primary keys use UUIDv4. UUIDv7 is not available in
  the Python/Postgres runtime without adding another dependency or extension.
- One Alembic migration per schema change. Each migration must downgrade cleanly;
  add a round-trip migration test for non-trivial schema changes.
- Entity resolution is conservative: never merge on name alone. Reuse an entity
  only when deterministic context rules produce exactly one match.
- Keep legacy `alum_name`/profile name columns until a later cleanup; identity is
  `entity_id`.

## Commands

- Install: `pip install -e .`
- Start local Postgres: `docker compose up -d postgres`
- Migrate: `alembic upgrade head`
- Run dev server: `uvicorn backend.main:app --reload`
- Test: `pytest -v`
- Eval: `python -m scripts.eval_extraction`
- Benchmark: `python -m scripts.bench_crawl`

## Things To Avoid

- Do not add Next.js, React, or any heavy frontend framework. Plain HTML/JS only.
- Do not scrape LinkedIn directly.
- Do not commit `.env`, `*.db`, `*.egg-info/`, `.ruff_cache/`, `.codex/`,
  `__pycache__/`, `.pytest_cache/`, `.venv/`, or generated eval output.
