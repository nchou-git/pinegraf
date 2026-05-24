from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import create_app


def test_health_returns_200(store) -> None:
    with TestClient(create_app(store)) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
