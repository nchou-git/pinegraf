from __future__ import annotations

import base64
import os
import subprocess
import sys
from collections.abc import Iterator

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from backend.config import get_settings
from backend.db.store import SCHEMA_TABLES, Store
from backend.ingestion import fetcher

PROD_DATABASE_HOSTS = {"34.181.200.174"}
PROD_DATABASE_NAMES = {"pinegraf"}


class FakeResponse:
    def __init__(
        self,
        url: str,
        status_code: int,
        body: bytes,
        headers: dict[str, str] | None = None,
        history: list["FakeResponse"] | None = None,
    ) -> None:
        self.url = url
        self.status_code = status_code
        self.content = body
        self.headers = headers or {"content-type": "text/html; charset=utf-8"}
        self.history = history or []

    @property
    def text(self) -> str:
        return self.content.decode("utf-8")

    def raise_for_status(self) -> None:
        if self.status_code < 400:
            return
        request = httpx.Request("GET", self.url)
        response = httpx.Response(self.status_code, request=request)
        raise httpx.HTTPStatusError("status error", request=request, response=response)


class FakeAsyncClient:
    Response = FakeResponse
    responses: dict[str, FakeResponse] = {}

    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs

    async def __aenter__(self) -> "FakeAsyncClient":
        return self

    async def __aexit__(self, *args) -> None:
        del args

    async def get(self, url: str, follow_redirects: bool = True) -> FakeResponse:
        del follow_redirects
        return self.responses[url]


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch) -> Iterator[None]:
    monkeypatch.setenv("PINEGRAF_ADMIN_PASSWORD", "Pinegrafposen$")
    monkeypatch.setenv("USE_MOCK_EMBEDDINGS", "true")
    monkeypatch.setenv("PINEGRAF_AUTO_PARSE", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def store(test_database_url: str) -> Iterator[Store]:
    _assert_not_production_database(test_database_url)
    db = Store(database_url=test_database_url)
    clean_store(db)
    yield db
    clean_store(db)


@pytest.fixture(scope="session")
def test_database_url() -> Iterator[str]:
    explicit_url = os.getenv("TEST_DATABASE_URL")
    if explicit_url:
        _assert_not_production_database(explicit_url)
        previous = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = explicit_url
        get_settings.cache_clear()
        yield explicit_url
        _restore_database_url(previous)
        return

    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError as exc:
        pytest.exit(
            "TEST_DATABASE_URL is required unless testcontainers is installed. "
            "Refusing to run tests against DATABASE_URL.",
            returncode=2,
        )
        raise exc

    try:
        with PostgresContainer("pgvector/pgvector:pg16") as postgres:
            url = postgres.get_connection_url().replace(
                "postgresql+psycopg2://", "postgresql+psycopg://"
            )
            previous = os.environ.get("DATABASE_URL")
            os.environ["DATABASE_URL"] = url
            get_settings.cache_clear()
            subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                cwd=os.getcwd(),
                env={**os.environ, "DATABASE_URL": url},
                check=True,
            )
            yield url
            _restore_database_url(previous)
    except Exception as exc:  # noqa: BLE001
        pytest.exit(
            f"Could not start isolated Postgres test database: {type(exc).__name__}: {exc}",
            returncode=2,
        )


def clean_store(store: Store) -> None:
    _assert_not_production_database(store.database_url)
    tables = ", ".join(f'"{table}"' for table in SCHEMA_TABLES)
    with store.engine.begin() as connection:
        connection.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))


def _assert_not_production_database(database_url: str) -> None:
    parsed = make_url(database_url)
    if parsed.host in PROD_DATABASE_HOSTS and parsed.database in PROD_DATABASE_NAMES:
        pytest.exit(
            "Refusing to run tests against the production Pinegraf database. "
            "Set TEST_DATABASE_URL to an isolated Postgres database.",
            returncode=2,
        )


def _restore_database_url(previous: str | None) -> None:
    if previous is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = previous
    get_settings.cache_clear()


@pytest.fixture
def admin_headers() -> dict[str, str]:
    token = base64.b64encode(b"pinegraf:Pinegrafposen$").decode("ascii")
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def fake_httpx(monkeypatch) -> type[FakeAsyncClient]:
    from backend.ingestion.runners import sitemap as sitemap_runner

    FakeAsyncClient.responses = {}
    fetcher._ROBOTS_CACHE.clear()
    fetcher._ROBOTS_LOAD_LOCKS.clear()
    monkeypatch.setattr(fetcher.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(sitemap_runner.httpx, "AsyncClient", FakeAsyncClient)
    return FakeAsyncClient


@pytest.fixture
def run_jobs_inline(monkeypatch, store) -> None:
    from backend import main as main_module
    from backend.jobs.run import run_from_env

    async def execute(run_id, mode: str) -> None:
        monkeypatch.setenv("PINEGRAF_RUN_ID", str(run_id))
        monkeypatch.setenv("PINEGRAF_MODE", mode)
        await run_from_env(store=store)

    monkeypatch.setattr(main_module, "execute_cloud_run_job", execute)
