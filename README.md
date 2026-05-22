# Pinegraf

Pinegraf is an OSINT research prototype for people and organizations. It starts
with a closed-world alumni seed list, crawls public non-LinkedIn pages, stores
source snapshots, extracts structured facts and relationships, resolves people
to conservative entity records, and answers analyst questions with citations.

The first target dataset is Tuck alumni, but the pipeline is built around
generic `person` and `organization` entities.

## Who It Is For

Pinegraf is for analysts and builders evaluating public-source research
workflows where source traceability matters. The app keeps raw page snapshots,
structured claim-level attributes, audit events, and natural-language query
paths in one local system.

## Local Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
cp .env.example .env
```

For local SQLite development, keep `DATABASE_URL=sqlite:///./pinegraf.db`.
For Postgres, start the bundled service and set the Postgres URL:

```bash
docker compose up -d postgres
```

Apply migrations and run the server:

```bash
alembic upgrade head
uvicorn backend.main:app --reload
```

Open `http://127.0.0.1:8000/`.

## Pipeline

```text
crawl -> parse -> resolve -> store -> query
```

- Crawl: `backend/pipeline/crawler.py` uses an async HTTP crawler with
  conditional GETs, per-host concurrency, source snapshotting, and mockable
  fetchers for tests.
- Parse: `backend/pipeline/parser.py` extracts profiles, facts, connections,
  projects, positions, and claim-level entity attributes from stored pages.
- Resolve: `backend/resolution/entity_resolver.py` never merges on name alone;
  it only reuses an entity when class year or current company context gives one
  exact match.
- Store: `backend/db/store.py` owns persistence and query helpers over the
  SQLAlchemy models in `backend/db/models.py`.
- Query: `backend/pipeline/query.py` answers strict structured questions or deep
  raw-page RAG questions with source URLs.

More detail: [docs/architecture.md](docs/architecture.md) and
[docs/data_model.md](docs/data_model.md).

## Migrations

Use Alembic for every schema change:

```bash
alembic upgrade head
alembic downgrade -1
alembic upgrade head
```

SQLite is supported for local tests and prototypes. Production should use
Postgres 14+.

## Evaluation

Run the seeded extraction eval:

```bash
python -m scripts.eval_extraction
```

The script runs migrations on a temporary SQLite database, crawls fixture pages,
parses with mock clients, compares extracted attributes to
`tests/eval/golden_set.json`, prints per-attribute precision/recall/F1, and
writes `eval_results.json`.

To add golden entries, edit `tests/eval/golden_set.json` and add matching JSON
fixtures in `tests/eval/fixtures/`. Fixture URLs use the slug generated from the
entity name, for example `https://fixtures.local/jane-doe.html`.

## Benchmark

Run the async crawler benchmark:

```bash
python -m scripts.bench_crawl
```

It serves 500 local fake pages across five localhost ports, crawls them with the
async crawler, prints pages/sec and wall time, and exits non-zero if the run
takes more than 30 seconds.

## Tests And Formatting

```bash
ruff check .
ruff format .
pytest -v
```

Tests must not hit real external APIs. Use mockable fetch, extraction,
validation, synthesis, and query clients.

## API

- `POST /crawl/start`, `GET /crawl/stream`: run and stream crawl jobs.
- `POST /parse/start?force=true`, `GET /parse/stream`: run and stream parse jobs.
- `POST /query`: answer `{"question": "...", "mode": "strict"|"deep"}`.
- `POST /lookup`: lookup-compatible query endpoint, audited.
- `POST /admin/login`: set the admin cookie.
- `GET /admin/audit`: admin-only audit event listing.
- `GET /profiles`, `/facts`, `/connections`, `/projects`: inspect stored data.
