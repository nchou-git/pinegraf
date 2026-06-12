from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend import main as main_module


def test_gated_data_endpoints_require_admin(admin_headers, monkeypatch) -> None:
    async def fake_ask_stream(*args, **kwargs) -> AsyncIterator[bytes]:
        del args, kwargs
        yield b'data: {"kind":"done"}\n\n'

    monkeypatch.setenv("PINEGRAF_DEMO_MODE", "false")
    monkeypatch.setattr(main_module, "engine_pool_config", lambda _engine: {})
    monkeypatch.setattr(main_module, "_warn_if_empty_database_since_deploy", lambda _store: None)
    monkeypatch.setattr(main_module, "append_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_module, "stats", lambda _store: {})
    monkeypatch.setattr(main_module, "list_claims", lambda _store, **kwargs: {"claims": []})
    monkeypatch.setattr(main_module, "ask_stream", fake_ask_stream)

    app_store = SimpleNamespace(engine=object())
    with TestClient(main_module.create_app(app_store)) as client:
        me = client.get("/api/me")
        assert me.status_code == 200
        assert me.json()["is_admin"] is False

        for path in ("/api/claims", "/api/stats"):
            assert client.get(path).status_code == 401
            assert client.get(path, headers=admin_headers).status_code == 200

        ask_payload = {"question": "Who founded Example?"}
        assert client.post("/api/ask", json=ask_payload).status_code == 401
        assert client.post("/api/ask", json=ask_payload, headers=admin_headers).status_code == 200
