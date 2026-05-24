from __future__ import annotations

import base64
from collections.abc import Iterator

import pytest

from backend.config import get_settings
from backend.db.store import Store


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
