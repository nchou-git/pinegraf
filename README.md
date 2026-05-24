# Pinegraf

Pinegraf is a five-layer intelligence pipeline:

1. Ingestion: `sources`, `source_runs`, `fetches`
2. Normalization: `documents`, `document_fetches`, `chunks`
3. Extraction: `extractor_runs`, `claims_raw`
4. Corroboration: entities, claims, evidence, conflicts, human signals
5. Projection: entity summaries and neighborhoods

The current app includes ingestion runners, normalization, extraction,
entity resolution, corroboration, projections, admin endpoints, and a plain
HTML/JS UI.

## Setup

```bash
pip install -e .
alembic upgrade head
uvicorn backend.main:app --reload
```

## Test

```bash
ruff format .
ruff check .
pytest -v
```

## Admin API

`/health` is public. `/admin/*` endpoints require HTTP Basic auth where the
password is `PINEGRAF_ADMIN_PASSWORD`.

Available endpoints:

- `POST /admin/sources`
- `POST /admin/runs/sitemap`
- `POST /admin/runs/seed`
- `POST /admin/runs/adhoc`
- `POST /admin/runs/{run_id}/normalize`
- `GET /admin/runs/{run_id}`
- `GET /admin/stats`
