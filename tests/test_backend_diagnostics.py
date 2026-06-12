from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend import main as main_module
from backend.web_api import _rank_documents


class AmbiguousVector:
    def __init__(self, values: list[float]) -> None:
        self._values = values

    def __bool__(self) -> bool:
        raise ValueError("ambiguous truth value")

    def __iter__(self):
        return iter(self._values)


def test_sources_endpoint_handles_empty_and_populated_stores(store, admin_headers) -> None:
    with TestClient(main_module.create_app(store)) as client:
        empty = client.get("/api/sources", headers=admin_headers)
        assert empty.status_code == 200
        assert empty.json() == {"sources": [], "archived_count": 0}

        store.upsert_source(kind="domain", identifier="example.edu", display_name="Example")

        populated = client.get("/api/sources", headers=admin_headers)
        assert populated.status_code == 200
        payload = populated.json()
        assert payload["archived_count"] == 0
        assert payload["sources"][0]["identifier"] == "example.edu"


def test_rank_documents_handles_pgvector_like_embeddings_without_truthiness() -> None:
    first = SimpleNamespace(cleaned_text="", embedding=AmbiguousVector([1.0, 0.0]))
    second = SimpleNamespace(cleaned_text="", embedding=AmbiguousVector([0.0, 1.0]))

    ranked = _rank_documents([second, first], [[1.0, 0.0]])

    assert ranked == [first, second]
