from __future__ import annotations

import base64
from collections.abc import Iterator

import httpx
import pytest

from backend.config import get_settings
from backend.db.store import Store
from backend.ingestion import fetcher


class FakeResponse:
    def __init__(self, url: str, status_code: int, body: bytes) -> None:
        self.url = url
        self.status_code = status_code
        self.content = body

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
    monkeypatch.setenv("PINEGRAF_ADMIN_PASSWORD", "pinegraf")
    monkeypatch.setenv("SITE_AUTH_PASSWORD", "")
    monkeypatch.setenv("USE_MOCK_EMBEDDINGS", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def store(tmp_path) -> Iterator[Store]:
    db = Store(f"sqlite:///{tmp_path / 'pinegraf-test.db'}")
    db.create_schema()
    yield db


@pytest.fixture
def admin_headers() -> dict[str, str]:
    token = base64.b64encode(b"admin:pinegraf").decode("ascii")
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def fake_httpx(monkeypatch) -> type[FakeAsyncClient]:
    FakeAsyncClient.responses = {}
    fetcher._ROBOTS_CACHE.clear()
    monkeypatch.setattr(fetcher.httpx, "AsyncClient", FakeAsyncClient)
    return FakeAsyncClient
