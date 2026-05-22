# Architecture

Pinegraf is a staged pipeline with a small FastAPI shell around independently
runnable modules.

```text
crawl -> parse -> resolve -> store -> query
```

## Data Flow

1. Seeds start in `data/alumni.csv` or caller-provided seed records.
2. `backend/pipeline/crawler.py` resolves a seed name to an entity, crawls
   public URLs with conditional GETs, and stores source snapshots in `raw_pages`.
3. `backend/pipeline/parser.py` reads unparsed snapshots, extracts structured
   evidence, validates it, writes facts/connections/projects/attributes, and
   synthesizes a profile projection.
4. `backend/resolution/entity_resolver.py` conservatively creates or reuses
   `entities` using alias plus class-year/current-company context.
5. `backend/db/store.py` owns writes, query helpers, full-text fallback, audit
   event storage, and snapshot helpers.
6. `backend/pipeline/query.py` answers analyst questions from structured rows in
   strict mode or from retrieved raw pages in deep mode.

## Modules

- `backend/main.py`: FastAPI route declarations and stage streaming.
- `backend/audit.py`: request audit middleware, admin cookie validation, audit
  response shaping.
- `backend/pipeline/page_fetcher.py`: sync/mock/fixture fetchers plus HTML text
  and link extraction.
- `backend/pipeline/crawler.py`: async crawler core and deprecated sync wrapper.
- `backend/pipeline/parser.py`: extraction, validation, synthesis, and parse job
  orchestration.
- `backend/pipeline/query.py`: strict structured query and deep raw-page RAG.
- `backend/db/models.py`: SQLAlchemy ORM schema.
- `backend/db/store.py`: database access boundary.
- `scripts/eval_extraction.py`: seeded extraction evaluation harness.
- `scripts/bench_crawl.py`: async crawler benchmark.

## Runtime Boundaries

External services are behind mockable clients. Tests use mock fetchers and mock
LLM clients. Database writes should go through `Store`; route handlers should
delegate behavior to modules rather than accumulating logic in `backend/main.py`.
