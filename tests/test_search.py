from backend.pipeline.search import MockSearchClient
from backend.pipeline.search import SerpAPISearchClient


def test_mock_search_returns_realistic_results() -> None:
    client = MockSearchClient()
    results = client.search_person("John Cena", "T95")

    assert len(results) >= 2
    assert "John Cena" in results[0].title
    assert results[0].link.startswith("https://")


def test_serpapi_search_client_returns_top_five_results(mocker) -> None:
    mocked_search = mocker.patch("serpapi.GoogleSearch")
    mocked_search.return_value.get_dict.return_value = {
        "organic_results": [
            {"title": f"Result {i}", "link": f"https://example.com/{i}", "snippet": f"Snippet {i}"}
            for i in range(1, 8)
        ]
    }

    client = SerpAPISearchClient(api_key="test-key")
    results = client.search_person("Jane Doe", "T24")

    assert len(results) == 5
    assert results[0].title == "Result 1"
    assert results[4].link == "https://example.com/5"
    mocked_search.assert_called_once_with(
        {
            "q": '"Jane Doe" Tuck T24 (LinkedIn OR Crunchbase OR company)',
            "api_key": "test-key",
            "engine": "google",
        }
    )
