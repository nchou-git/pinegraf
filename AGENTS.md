# Pinegraf

Pipeline: take a list of alumni → enrich each one with structured data from web search → store in DB → expose a query interface where a user asks natural-language questions and a cheap LLM answers from the DB.

## Stack
- Python 3.11+, FastAPI, SQLAlchemy, SQLite (dev), pytest
- OpenAI Python SDK; `gpt-5.3` for extraction, `gpt-5.3-mini` for query
- SerpAPI for Google search (`google-search-results` package)
- Vanilla HTML/JS frontend served by FastAPI from /static

## Conventions
- All secrets via .env (use python-dotenv). Never hardcode keys.
- Every external API call must have a mockable interface — tests must not hit real APIs.
- Pipeline stages are pure functions where possible; side effects isolated to db/store.py.
- Type hints everywhere. Pydantic models for LLM I/O.
- Format with `ruff format`. Lint with `ruff check`.

## Commands
- Install: `pip install -e .`
- Run dev server: `uvicorn backend.main:app --reload`
- Test: `pytest -v`

## Things to avoid
- Don't add Next.js, React, or any heavy frontend framework. Plain HTML/JS only.
- Don't write code that scrapes LinkedIn directly — use search results / public pages only.
- Don't commit .env, *.db, or __pycache__.
