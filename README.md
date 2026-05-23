# Pinegraf

People-OSINT prototype for building a source-linked alumni knowledge graph.
Pinegraf crawls public pages, imports structured seed data, extracts graph facts
with cost-tracked LLM calls, reconciles entities, and answers analyst questions
from structured rows or cited raw-page chunks.

Status: prototype. Schema and APIs are still expected to change.

## What It Does

- **Seed** - imports `data/alum_data.xlsx` into `entities` and
  `entity_attributes` with `source='alumni_xlsx_v2'`.
- **Crawl** - fetches configured public pages, stores raw HTML snapshots and
  cleaned text in `raw_pages`, and keeps conditional-fetch metadata.
- **Clean** - strips noisy HTML and learns per-host prefix/suffix boilerplate in
  `host_boilerplate`.
- **Parse** - chunks pages with `tiktoken`, triages chunks, extracts explicit
  subject-predicate-object claims, caches chunk responses, records token spend
  in `llm_usage`, and emits progress events for the admin UI.
- **Resolve** - conservatively resolves entities with deterministic context
  rules plus optional pgvector-backed name/context embeddings.
- **Reconcile** - consolidates attributes and infers `co_worked_on`,
  `co_worked_at`, and `classmate` relationships with derivation metadata.
- **Research** - expands analyst questions, retrieves chunk evidence with
  trigram plus vector search, and answers with inline source citations.
- **Enrich** - can add Wikidata attributes using idempotent
  `source='wikidata:<qid>'` rows.

## Architecture

```text
data/alum_data.xlsx      sitemap/seed URLs         Wikidata
        |                       |                     |
        v                       v                     v
 entities + attributes      SiteCrawler        enrich_wikidata.py
        |                       |
        |                  raw_pages + snapshots
        |                       |
        |          clean_html + host_boilerplate
        |                       |
        |           chunk_page + page_chunks
        |                       |
        |       extraction_cache + llm_usage
        |                       |
        +------------> claims -> facts, projects, connections
                             |
                 reconcile_entities.py
                             |
             entity_consolidated + inferred edges
                             |
       FastAPI /lookup /research /entity/{id} /admin/*
```

See `docs/graph_schema.md`, `docs/query_examples.md`, and `docs/sources.md`
for the current graph details.

## Requirements

- Python 3.11+
- Postgres 14+ for production
- `pgvector` and `pg_trgm` extensions for embedding resolution and hybrid
  retrieval
- SQLite is supported for tests and local mock-mode development

The included Docker service uses `pgvector/pgvector:pg16`:

```bash
docker compose up -d postgres
```

If you use a host Postgres install, make sure `CREATE EXTENSION vector` works
before running migrations that add vector columns.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

cp .env.example .env
# edit .env: set OPENAI_API_KEY, DATABASE_URL, PINEGRAF_ADMIN_PASSWORD,
# SITE_AUTH_PASSWORD, and crawler config
# (CRAWL_SITEMAP_URLS, CRAWL_ALLOWED_DOMAINS, etc.)

alembic upgrade head
```

## Run

```bash
uvicorn backend.main:app --reload
```

Open:

- `http://127.0.0.1:8000` - Lookup, Research, and Connections UI
- `http://127.0.0.1:8000/admin` - admin panel for crawl, parse, preview,
  resource usage, and extraction audits

### Deploy

Fly.io deployment files are included but deployment is manual:

```bash
fly launch --no-deploy
fly secrets set \
  OPENAI_API_KEY=... \
  DATABASE_URL=... \
  PINEGRAF_ADMIN_PASSWORD=... \
  SITE_AUTH_USER=pinegraf \
  SITE_AUTH_PASSWORD=...
fly deploy
```

Set any runtime toggles the deployment needs with `fly secrets set` as well:
`USE_MOCK_FETCH`, `USE_MOCK_EXTRACT`, `USE_MOCK_QUERY`, `CRAWL_SEED_URLS`,
`CRAWL_SITEMAP_URLS`, `CRAWL_ALLOWED_DOMAINS`, `CRAWL_MAX_PAGES`, and
`CRAWL_MAX_DEPTH`. Do not put real secrets in `fly.toml`, the Dockerfile, or a
committed `.env` file.

## Pipeline Commands

```bash
python -m scripts.import_alumni_xlsx
python -m scripts.rebuild_page_text
python -m scripts.backfill_entity_embeddings
python -m scripts.reconcile_entities
python -m scripts.audit_extraction --sample-size 30
python -m scripts.enrich_wikidata --limit 100
```

## Configuration

Common `.env` settings:

```env
USE_MOCK_FETCH=false
USE_MOCK_EXTRACT=false
USE_MOCK_QUERY=false
EXTRACTION_TIER_MODE=cascade
PARSE_CONCURRENCY=8
CRAWL_SITEMAP_URLS=https://example.edu/sitemap.xml
CRAWL_SEED_URLS=https://example.edu/
CRAWL_ALLOWED_DOMAINS=example.edu
CRAWL_MAX_PAGES=5000
```

Settings are cached at process start; restart uvicorn after changing `.env`.

`EXTRACTION_TIER_MODE` accepts `mini_only`, `cascade`, or `frontier_only`.
Cascade uses `gpt-5.4-mini` first and escalates low-confidence chunks to
`gpt-5.4`. Research answers use the frontier model configured in
`backend/pipeline/query.py`.

## Mock Mode

`USE_MOCK_FETCH=true`, `USE_MOCK_EXTRACT=true`, `USE_MOCK_QUERY=true` run the
full pipeline against canned data with no external HTTP or OpenAI calls. Tests
use mockable clients and should never require real API calls.

## Tests

```bash
ruff format .
ruff check .
pytest -v
```

## Database

Tables of note:

- `raw_pages` - fetched page snapshots with cleaned text and compressed raw HTML.
- `host_boilerplate` - per-host learned prefix/suffix boilerplate.
- `page_chunks` - chunk text and optional embeddings used for hybrid retrieval.
- `entities`, `entity_aliases`, `entity_attributes` - canonical identity and
  source-linked claims.
- `claims` - claim-native extraction rows with explicit subject and object.
- `facts`, `projects`, `connections` - projections and inferred graph evidence.
- `entity_consolidated` - reconciled profile fields with source row ids.
- `extraction_cache` - chunk-level triage and extraction cache.
- `llm_usage` - one row per LLM or embedding call with token and dollar totals.
- `audit_runs` and `audit_events` - quality-audit outputs and request audit log.

## Layout

```text
backend/
  main.py                 FastAPI route declarations
  config.py               env-driven settings
  audit.py                auth + audit middleware
  db/
    models.py             SQLAlchemy models
    store.py              DB queries and writes
  pipeline/
    crawler.py            async sitemap crawler
    page_fetcher.py       fetchers and HTML text extraction
    parser.py             chunking, extraction, validation, parse orchestration
    query.py              strict and hybrid research query clients
    reconcile.py          consolidation and graph inference
  resolution/
    entity_resolver.py    context + embedding entity resolution
    embeddings.py         mockable embedding clients
  sources/
    wikidata.py           Wikidata enrichment source
frontend/
  index.html, app.js      user-facing UI
  admin.html, admin.js    admin panel
scripts/                  one-shot ingest, rebuild, audit, enrichment scripts
alembic/                  migrations
tests/                    pytest suite
```

## Security Note

The current admin auth is a single shared password compared in constant time,
with a session cookie signed by HMAC. It is sufficient for a single-user
prototype. It is **not** sufficient for multi-user production; replace it with
per-user accounts and a real session store before deploying.

`.env` is gitignored. Never commit secrets.
