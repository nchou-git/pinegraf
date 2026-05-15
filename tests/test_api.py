from fastapi.testclient import TestClient

from backend.main import app


def test_enrich_then_query_flow() -> None:
    client = TestClient(app)

    enrich_response = client.post("/enrich")
    assert enrich_response.status_code == 200
    assert enrich_response.json()["enriched_count"] == 5

    query_response = client.post("/query", json={"question": "Who works at Acme Corp?"})
    assert query_response.status_code == 200
    assert "Acme alumni" in query_response.json()["answer"]
