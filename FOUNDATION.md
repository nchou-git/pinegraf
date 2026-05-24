# Week 1 Foundation

## Summary

Shipped a clean five-layer foundation:

- Nuked legacy pipeline, resolution, extraction/discovery/inference/storage code,
  old migrations, old frontend pages, stale docs, old tests, and transient
  artifacts.
- Added one base migration: `0001_intelligence_pipeline_foundation`.
- Added Layer 1 ingestion tables and runners for sitemap, seed, and adhoc URL
  runs.
- Added Layer 2 normalization for HTML cleaning, dedupe, chunking, and
  embeddings.
- Added shell tables for extraction, corroboration, and projection layers.
- Added minimal FastAPI admin endpoints and startup source seeding.
- Kept `/health` public and removed user-facing lookup/research/graph routes.

Primary keys use UUIDv4. UUIDv7 is not available in the Python/Postgres runtime
without adding another dependency or database extension.

## Verification Status

Local gate passed:

```bash
ruff format .
ruff check .
pytest -v
```

Fly deploy completed and published image
`pinegraf:deployment-01KSC8TZRMA5QVBK2ZVN354GAK`, but the production machine
does not currently pass `/health`. Startup fails before Uvicorn because the Fly
`DATABASE_URL` points at a Neon project that is rejecting connections with:

```text
Your project has exceeded the data transfer quota. Upgrade your plan to increase limits.
```

I did not change secrets or weaken startup behavior to hide the database
failure. The Neon/Fly-side database verification and startup source seed are
pending until that quota issue is resolved.

## Table Counts

Verified against the migrated local Postgres configured in `.env`:

| Table | Rows |
| --- | ---: |
| sources | 2 |
| source_runs | 0 |
| fetches | 0 |
| documents | 0 |
| document_fetches | 0 |
| chunks | 0 |
| extractor_runs | 0 |
| claims_raw | 0 |
| entities | 0 |
| entity_aliases | 0 |
| entity_mentions | 0 |
| claims | 0 |
| claim_evidence | 0 |
| claim_conflicts | 0 |
| human_signals | 0 |
| entity_summary | 0 |
| entity_neighborhood | 0 |

The old local tables are gone; only the new schema tables plus
`alembic_version` remain. `pg_trgm` and `vector` remain installed.

## Sample Admin Curls

```bash
ADMIN_AUTH='admin:<PINEGRAF_ADMIN_PASSWORD>'

curl -u "$ADMIN_AUTH" -X POST https://pinegraf.fly.dev/admin/sources \
  -H 'content-type: application/json' \
  -d '{"kind":"domain","identifier":"example.com","trust_weight":0.5}'

curl -u "$ADMIN_AUTH" -X POST https://pinegraf.fly.dev/admin/runs/sitemap \
  -H 'content-type: application/json' \
  -d '{"source_id":"<source-id>","sitemap_url":"https://example.com/sitemap.xml"}'

curl -u "$ADMIN_AUTH" -X POST https://pinegraf.fly.dev/admin/runs/adhoc \
  -H 'content-type: application/json' \
  -d '{"source_id":"<source-id>","urls":["https://example.com/page"]}'

curl -u "$ADMIN_AUTH" -X POST https://pinegraf.fly.dev/admin/runs/seed \
  -H 'content-type: application/json' \
  -d '{"source_id":"<source-id>","seed_file_path":"data/alum_data.xlsx"}'

curl -u "$ADMIN_AUTH" -X POST \
  https://pinegraf.fly.dev/admin/runs/<run-id>/normalize

curl -u "$ADMIN_AUTH" https://pinegraf.fly.dev/admin/runs/<run-id>
curl -u "$ADMIN_AUTH" https://pinegraf.fly.dev/admin/stats
```

## Not Built Yet

- Extraction logic beyond table shells.
- Entity resolution logic.
- Corroboration scoring/conflict logic.
- Projection workers.
- User-facing Directory, Ask, or Graph tabs.
- Legacy `/lookup`, `/research`, `/connections`, `/admin/crawl`,
  `/admin/parse`, or `/admin/audit` endpoints.

## Week 2 Entry Point

Start with `backend/normalization/runner.py` and `backend/ingestion/orchestrator.py`
to drive documents into `chunks`, then add extraction workers that populate
`extractor_runs` and `claims_raw` from pending chunks.

## Notification

`ntfy.sh/pinegraf-nate-build` was not pinged because the deployed Fly machine is
not healthy while Neon rejects the production database connection.
