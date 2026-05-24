# Week 2

## Summary

Week 2 adds working extraction, resolution, corroboration, projection, and a
plain HTML/JS/CSS application shell on top of the Week 1 foundation.

Implemented backend packages:

- `backend/extraction/`: claim prompts, cascade extraction, cost accounting,
  and `extract_pending`.
- `backend/resolution/`: mention embeddings, deterministic resolution, entity
  creation, and `resolve_pending`.
- `backend/corroboration/`: raw claim promotion, evidence weighting, conflict
  detection, confidence scoring, and `corroborate_pending`.
- `backend/projections/`: rebuilds `entity_summary` and
  `entity_neighborhood`.
- `backend/pipeline/`: full-pipeline orchestrator with in-memory SSE progress.
- `backend/web_api.py`: user-facing graph, source, feedback, and ask helpers.

Implemented frontend:

- `frontend/index.html`
- `frontend/styles.css`
- `frontend/app.js`

The frontend is framework-free and uses the Dartmouth visual constraints:
white background, Dartmouth green, Georgia for editorial/entity names, Arial for
utility text, squared controls, and hairline borders.

## Endpoint Surface

Public health:

- `GET /health`

Site-authenticated API:

- `GET /`
- `GET /api/me`
- `GET /api/stats`
- `GET /api/sources`
- `GET /api/directory`
- `GET /api/entity/{entity_id}`
- `GET /api/claim/{claim_id}`
- `POST /api/ask`
- `POST /api/feedback`

Admin API:

- `GET /admin`
- `POST /admin/sources`
- `POST /admin/runs/sitemap`
- `POST /admin/runs/seed`
- `POST /admin/runs/adhoc`
- `POST /admin/runs/{run_id}/normalize`
- `POST /admin/runs/{run_id}/pipeline`
- `GET /admin/runs/{run_id}/stream`
- `GET /admin/runs/{run_id}`
- `GET /admin/stats`
- `GET /admin/conflicts`
- `POST /admin/conflicts/{conflict_id}/resolve`
- `POST /admin/sources/{source_id}/trust`
- `POST /admin/reset-extraction`

## Pipeline Call Graph

`run_full_pipeline(workspace_id, source_run_id)` runs:

1. `normalization.runner.normalize_pending(source_run_id=...)`
2. `extraction.runner.extract_pending(...)`
3. `resolution.runner.resolve_pending(...)`
4. `corroboration.runner.corroborate_pending(...)`
5. `projections.runner.rebuild_projections(...)`

Progress is written into `source_runs.stats` and emitted through the in-memory
SSE stream used by `GET /admin/runs/{run_id}/stream`.

## Cost Expectations

Extraction cost is recorded on `extractor_runs.cost_usd` from model token usage.
The configured defaults are:

- Cheap pass: `CHEAP_MODEL`, default `gpt-4o-mini`.
- Frontier pass: `FRONTIER_MODEL`, default `gpt-4o`.

Using the pricing table in `backend/extraction/cost.py`, 1000 pages at roughly
two 512-token chunks per page is expected to be well under $1 for cheap-pass
extraction when frontier escalation is rare. A 10% frontier escalation rate adds
roughly another $1 to $2 per 1000 pages, depending on output length.

Embeddings and Ask requests add separate OpenAI usage. The Ask endpoint caches
identical questions for one hour in memory.

## Verification

Local checks:

- `ruff format .`
- `ruff check .`
- `pytest -v`

Current suite covers:

- Week 1 health, schema, admin, ingestion, and normalization paths.
- Week 2 extraction heuristic.
- Resolution exact matching with Tuck suffix normalization.
- Admin and site auth behavior for new endpoints.
- Synthetic end-to-end pipeline from fetch to claims, evidence, summaries, and
  neighborhoods.

## Deferred To Week 3

- Physical workspace scoping. The API accepts `workspace`, but the Week 1 schema
  has no workspace tables or workspace foreign keys.
- LLM-assisted borderline entity resolution. The deterministic waterfall is in
  place; the LLM confirmation branch remains conservative.
- Rich graph edge drilldown. The endpoint exists, but the frontend currently
  shows edge summaries and should next call `GET /api/claim/{claim_id}` for full
  evidence.
- External IDs and Wikidata badges.
- Persistent pub/sub. Pipeline progress is in-memory, which is acceptable for
  the current single-instance Cloud Run configuration.
- True streaming from OpenAI for Ask. The endpoint streams SSE to the browser,
  but model output is currently generated before being tokenized to the client.
