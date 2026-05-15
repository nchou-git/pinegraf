from backend.pipeline.extract import MockExtractClient
from backend.pipeline.search import SearchResult


def test_mock_extract_returns_structured_profile() -> None:
    client = MockExtractClient()
    results = [
        SearchResult(
            title="John Cena - LinkedIn",
            link="https://example.com/john-cena",
            snippet="John Cena (T95) is currently a Senior Manager at Acme Corp.",
        )
    ]

    profile = client.extract_profile("John Cena", results)

    assert profile.name == "John Cena"
    assert profile.current_company == "Acme Corp"
    assert profile.current_title == "Senior Manager"
    assert "Beta Inc" in profile.past_companies
