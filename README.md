# Pinegraf

Pinegraf is a five-layer intelligence pipeline:

1. Ingestion: `sources`, `source_runs`, `fetches`
2. Normalization: `documents`, `document_fetches`, `chunks`
3. Extraction: `extractor_runs`, `claims_raw`
4. Corroboration: entities, claims, evidence, conflicts, human signals
5. Projection: entity summaries and neighborhoods

The app uses one Cloud SQL Postgres database for local development, tests, and
production. `DATABASE_URL` is required and must point at the live Cloud SQL
instance with `sslmode=require`.

## Setup

```bash
pip install -e .
alembic upgrade head
uvicorn backend.main:app --reload
```

## Database Access

Local development connects directly to Cloud SQL public IP:

```bash
DATABASE_URL="postgresql+psycopg://pinegraf_app:PASSWORD@34.181.200.174:5432/pinegraf?sslmode=require"
```

If your public IP changes, authorize it before running the app or tests:

```bash
gcloud sql instances patch pinegraf-db \
  --project=pinegraf-prod \
  --authorized-networks="$(curl -sS https://api.ipify.org)/32"
```

Cloud Run also uses the direct public database URL. Without a fixed Cloud Run
egress IP, the instance must allow managed egress to reach Postgres, or the
service must be moved behind controlled egress and that IP authorized.

## Test

Tests run against the live Cloud SQL database and truncate Pinegraf tables before
and after each database-backed test.

```bash
ruff format .
ruff check .
pytest -v
```

## Admin API

`/health` is public. `/admin/*` endpoints require admin auth where the password
is `PINEGRAF_ADMIN_PASSWORD`.

Current admin paths are source management, per-source crawl/parse, run streams,
conflicts, and login/logout.
