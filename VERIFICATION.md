# Verification

## Ruff

```text
ruff format .
73 files left unchanged

ruff check .
All checks passed!
```

## Pytest

```text
87 passed in 13.72s
```

## Route Smoke Checks

Local command used mock/test settings on port `8010`:

```text
GET /health -> 200
GET / without site auth -> 401
GET / with site auth -> 200
POST /lookup with {} and site auth -> 200
GET /admin with site auth -> 200, admin login page served
GET /stats with site auth -> 200
GET /version with site auth -> 200
```

## Docker Build

```text
Skipped: docker is not available in this environment.
The command 'docker' could not be found in this WSL 2 distro.
```

## Warnings

- The smoke server used SQLite and mock mode intentionally to avoid touching
  production data or spending OpenAI dollars.
- Docker image build still needs to be run in an environment with Docker
  available.
