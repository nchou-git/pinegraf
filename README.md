# Pinegraf

Pinegraf is being rebuilt as a five-layer intelligence pipeline:

1. Ingestion: `sources`, `source_runs`, `fetches`
2. Normalization: `documents`, `document_fetches`, `chunks`
3. Extraction: `extractor_runs`, `claims_raw`
4. Corroboration: entities, claims, evidence, conflicts, human signals
5. Projection: entity summaries and neighborhoods

Week 1 ships the foundation schema, Layer 1 ingestion runners, Layer 2
normalization, and minimal admin endpoints. Extraction, resolution,
corroboration logic, projections, and user-facing UI are intentionally not
built yet.

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
