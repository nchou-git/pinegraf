# Pinegraf

People-OSINT prototype. Crawls public web pages, extracts structured profile data and relationships, and answers natural-language questions about the resulting corpus with citations.

Status: early prototype. Schema and API will change.

## What it does

- **Crawl** — fetches pages from configured sitemaps and seed URLs, stores raw HTML and extracted text in Postgres. Respects robots.txt, conditional GETs (ETag / Last-Modified), per-host pacing.
- **Parse** — LLM extracts profiles, companies, positions, education, and relationships from each page; validates and stores structured rows.
- **Lookup** — structured DB filter by name / company / class year. No AI.
- **Research** — natural-language questions answered from the raw page corpus with source citations.
- **Admin** — password-gated panel to trigger crawl/parse jobs and watch live progress.

## Architecture

```text
Sitemap/seed URLs
       |
       v
   SiteCrawler (async, per-host pacing)
       |
       v
   raw_pages (Postgres)
       |
       v
     Parser (OpenAI)
       |
       v
   entities, attributes, connections, projects
       |
       v
   FastAPI /lookup    (structured query, no LLM)
   FastAPI /research  (deep query, LLM over raw pages with citations)
```

See `docs/architecture.md` and `docs/data_model.md` for details.

## Requirements

- Python 3.11+
- Postgres 14+ (SQLite fallback for local dev only)

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

cp .env.example .env
# edit .env: set OPENAI_API_KEY, DATABASE_URL, PINEGRAF_ADMIN_PASSWORD,
# and crawler config (CRAWL_SITEMAP_URLS, CRAWL_ALLOWED_DOMAINS, etc.)

alembic upgrade head
```

## Run

```bash
uvicorn backend.main:app --reload
```

Open:

- `http://127.0.0.1:8000` — user UI (Lookup + Research)
- `http://127.0.0.1:8000/admin` — admin panel (Crawl + Parse), password from `PINEGRAF_ADMIN_PASSWORD`

## Configure a crawl

In `.env`:

```env
USE_MOCK_FETCH=false
CRAWL_SITEMAP_URLS=https://example.edu/sitemap.xml
CRAWL_SEED_URLS=https://example.edu/
CRAWL_ALLOWED_DOMAINS=example.edu
CRAWL_MAX_PAGES=5000
```

Settings are cached at process start; restart uvicorn after changing `.env`.

## Mock mode

`USE_MOCK_FETCH=true`, `USE_MOCK_EXTRACT=true`, `USE_MOCK_QUERY=true` run the full pipeline against canned data with no external HTTP / OpenAI calls. Useful for UI testing and offline dev.

## Tests

```bash
pytest -v
```

## Database

Tables of note:

- `raw_pages` — one row per fetched page. Includes `page_text`, `raw_html_gz`, `content_sha256`, ETag/Last-Modified, fetch timestamp.
- `entities`, `entity_aliases`, `entity_attributes` — canonical entity layer with claim-level provenance.
- `alumni_profiles` — denormalized profile view used by `/lookup`. Kept in sync by the parser.
- `audit_events` — append-only log of every `/lookup`, `/research`, and `/admin/*` request.

## Layout

```text
backend/
  main.py              FastAPI routes (thin)
  config.py            env-driven settings
  audit.py             auth + audit middleware
  db/
    models.py          SQLAlchemy models
    store.py           DB queries and writes
  pipeline/
    crawler.py         async sitemap crawler
    page_fetcher.py    sync httpx client used inside crawler
    parser.py          extractor + validator + synthesizer (LLM)
    query.py           strict / deep query clients
  resolution/
    entity_resolver.py name -> entity_id resolution
frontend/
  index.html, app.js   user-facing UI
  admin.html, admin.js admin panel
alembic/               migrations
tests/                 pytest suite
```

## Security note

The current admin auth is a single shared password compared in constant time, with a session cookie signed by HMAC. It is sufficient for a single-user prototype. It is **not** sufficient for multi-user production — replace with per-user accounts (bcrypt/argon2) and a real session store before deploying.

`.env` is gitignored. Never commit secrets.
