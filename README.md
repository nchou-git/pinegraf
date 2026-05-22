# Pinegraf

Pinegraf maps the people behind an alumni network. It starts from `data/alumni.csv`, crawls public pages, parses those stored pages into source-linked structured tables, and answers natural-language questions in strict or deep RAG mode.

## Architecture

Pinegraf runs as three decoupled stages:

1. **Crawl**: `httpx` page fetches from seed URLs. This stage never calls an LLM. Cleaned page text is stored in `raw_pages` and re-runs skip existing `(alum_name, source_url)` rows.
2. **Parse**: LLM extraction, validation, and synthesis over stored `raw_pages`. Structured facts, connections, and projects point back to `source_raw_page_id`.
3. **Query**: Strict mode reads validated structured rows only. Deep mode retrieves raw pages with Postgres full-text search, falls back to SQLite page order in dev, and asks the LLM to cite page sources.

## Local Setup

1. Create and activate a Python 3.11+ virtualenv.
2. Start Postgres:
   ```bash
   docker compose up -d postgres
   ```
3. Install dependencies:
   ```bash
   pip install -e .
   ```
4. Copy env file:
   ```bash
   cp .env.example .env
   ```
5. Run the app:
   ```bash
   uvicorn backend.main:app --reload
   ```

Open `http://127.0.0.1:8000/`.

## Database

`.env.example` uses:

```text
DATABASE_URL=postgresql+psycopg://pinegraf:pinegraf@localhost:5432/pinegraf
```

The app uses SQLAlchemy `create_all()` for local schema creation. On Postgres it also creates a guarded GIN full-text index:

```sql
CREATE INDEX IF NOT EXISTS idx_raw_pages_page_text_fts
ON raw_pages USING GIN (to_tsvector('english', page_text));
```

If Postgres is not available, Pinegraf falls back to `sqlite:///./pinegraf.db` for laptop dev and logs:

```text
Running on SQLite - this is dev only. Production deployment must use Postgres.
```

Production deployment expects Postgres 14+. The schema is portable; no Postgres-specific features other than JSONB and the GIN tsvector index. The tsvector index can be dropped if FTS is not needed.

## Environment Variables

- `OPENAI_API_KEY`: OpenAI API key for parse and query stages.
- `DATABASE_URL`: SQLAlchemy database URL.
- `USE_MOCK_FETCH`, `USE_MOCK_EXTRACT`, `USE_MOCK_QUERY`: set to `true` for local deterministic mocks.
- `CRAWL_PAGES_PER_ALUM`: maximum deduped URLs fetched per alum.

## API

- `POST /crawl/start`: starts the crawl stage.
- `GET /crawl/stream`: streams crawl SSE events.
- `POST /parse/start?force=true`: starts the parse stage; `force=true` reparses all raw pages.
- `GET /parse/stream`: streams parse SSE events.
- `POST /query`: accepts `{"question": "...", "mode": "strict"|"deep"}`.
- `GET /profiles`, `/facts`, `/connections`, `/projects`: read stored structured data.

## Deployment Notes For Tuck IT

- Postgres 14+ is required for production.
- Set the production connection string in `DATABASE_URL`.
- No Postgres extensions are required. Full-text search uses built-in `tsvector`; `pgvector` is optional for future semantic search.
- Use standard `pg_dump`/`pg_restore` for backups and restores.
- The app server is stateless. Horizontal scaling is supported when workers share the same Postgres database.

## Test

```bash
pytest -v
```

## Extraction Eval

Run the seeded golden extraction eval:

```bash
python -m scripts.eval_extraction
```

Add new golden entries in `tests/eval/golden_set.json` and add matching page
fixtures in `tests/eval/fixtures/`. Fixture URLs should use the slug generated
from the entity name, for example `https://fixtures.local/jane-doe.html`.

## Lint/Format

```bash
ruff check .
ruff format .
```
