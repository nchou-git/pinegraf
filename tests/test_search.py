from backend.pipeline.search import MockSearchClient, SerpAPISearchClient, build_person_queries


def test_mock_search_returns_realistic_results() -> None:
    client = MockSearchClient()
    results = client.search_person("John Cena", "T95")

    assert len(results) >= 2
    assert "John Cena" in results[0].title
    assert results[0].link.startswith("https://")


def test_serpapi_search_client_returns_top_three_results_per_query(mocker) -> None:
    mocked_search = mocker.patch("serpapi.GoogleSearch")

    def fake_google_search(params):
        suffix = "a" if "Tuck T24" in params["q"] else "b"
        fake_search = mocker.Mock()
        fake_search.get_dict.return_value = {
            "organic_results": [
                {
                    "title": f"Result {suffix}{i}",
                    "link": f"https://example.com/{suffix}/{i}",
                    "snippet": f"Snippet {suffix}{i}",
                }
                for i in range(1, 8)
            ]
        }
        return fake_search

    mocked_search.side_effect = fake_google_search

    client = SerpAPISearchClient(api_key="test-key")
    results = client.search_person("Jane Doe", "T24")

    assert len(results) == 6
    assert len(build_person_queries("Jane Doe", "T24")) == 2
    assert results[0].title == "Result a1"
    assert results[2].link == "https://example.com/a/3"
    assert results[3].title == "Result b1"
    assert results[5].link == "https://example.com/b/3"
    assert mocked_search.call_count == len(build_person_queries("Jane Doe", "T24"))
    mocked_search.assert_any_call(
        {
            "q": '"Jane Doe" Tuck T24 (LinkedIn OR Crunchbase OR company)',
            "api_key": "test-key",
            "engine": "google",
            "num": 3,
        }
    )
