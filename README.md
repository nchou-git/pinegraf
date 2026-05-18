# Pinegraf

Pinegraf maps the people behind any network. Starting with Tuck alumni - extracts profiles, projects, and relationships from public web data, then answers natural-language questions about the graph it builds.

## Setup

1. Create and activate a Python 3.11+ virtualenv.
2. Install dependencies:
   ```bash
   pip install -e .
   ```
3. Copy env file:
   ```bash
   cp .env.example .env
   ```

## Run

```bash
uvicorn backend.main:app --reload
```

Open `http://127.0.0.1:8000/`.

## API

- `POST /enrich` enriches alumni from `data/alumni.csv` and stores profiles.
- `POST /query` answers questions using stored alumni profiles.

## Test

```bash
pytest -v
```

## Lint/Format

```bash
ruff check .
ruff format .
```
