from backend.pipeline.search import MockSearchClient


def test_mock_search_returns_realistic_results() -> None:
    client = MockSearchClient()
    results = client.search_person("John Cena", "T95")

    assert len(results) >= 2
    assert "John Cena" in results[0].title
    assert results[0].link.startswith("https://")
