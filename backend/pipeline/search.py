from __future__ import annotations

from dataclasses import dataclass

import serpapi


@dataclass
class SearchResult:
    title: str
    link: str
    snippet: str


class SearchClient:
    def search_person(self, name: str, class_year: str) -> list[SearchResult]:
        raise NotImplementedError


class MockSearchClient(SearchClient):
    def search_person(self, name: str, class_year: str) -> list[SearchResult]:
        return [
            SearchResult(
                title=f"{name} - LinkedIn",
                link=f"https://example.com/{name.lower().replace(' ', '-')}-linkedin",
                snippet=f"{name} ({class_year}) is currently a Senior Manager at Acme Corp.",
            ),
            SearchResult(
                title=f"{name} - Company Bio",
                link=f"https://example.com/{name.lower().replace(' ', '-')}-bio",
                snippet=f"Previously worked at Beta Inc and Gamma LLC.",
            ),
        ]


class SerpApiSearchClient(SearchClient):
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def search_person(self, name: str, class_year: str) -> list[SearchResult]:
        params = {
            "q": f'"{name}" Tuck {class_year} (LinkedIn OR Crunchbase OR company)',
            "api_key": self.api_key,
            "engine": "google",
        }
        response = serpapi.GoogleSearch(params).get_dict()
        organic_results = response.get("organic_results", [])
        return [
            SearchResult(
                title=item.get("title", ""),
                link=item.get("link", ""),
                snippet=item.get("snippet", ""),
            )
            for item in organic_results[:5]
        ]


class SerpAPISearchClient(SerpApiSearchClient):
    """Alias with requested naming."""
