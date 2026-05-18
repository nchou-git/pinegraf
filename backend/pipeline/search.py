from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import serpapi


@dataclass
class SearchResult:
    title: str
    link: str
    snippet: str


class SearchClient:
    def search_person(self, name: str, class_year: str) -> list[SearchResult]:
        raise NotImplementedError


def build_person_queries(name: str, class_year: str) -> list[str]:
    quoted_name = f'"{name}"'
    return [
        f"{quoted_name} Tuck {class_year} (LinkedIn OR Crunchbase OR company)",
        f"{quoted_name} Dartmouth Tuck",
    ]


def _dedupe_results(results: Iterable[SearchResult], limit: int) -> list[SearchResult]:
    seen_links: set[str] = set()
    deduped: list[SearchResult] = []
    for result in results:
        link = result.link.strip()
        if not link or link in seen_links:
            continue
        seen_links.add(link)
        deduped.append(result)
        if len(deduped) >= limit:
            break
    return deduped


class MockSearchClient(SearchClient):
    def search_person(self, name: str, class_year: str) -> list[SearchResult]:
        slug = name.lower().replace(" ", "-").replace(".", "")
        return [
            SearchResult(
                title=f"{name} - LinkedIn",
                link=f"https://example.com/{slug}-linkedin",
                snippet=f"{name} ({class_year}) is currently a Senior Manager at Acme Corp.",
            ),
            SearchResult(
                title=f"{name} - Company Bio",
                link=f"https://example.com/{slug}-bio",
                snippet=(
                    f"{name} previously worked at Beta Inc and Gamma LLC after Tuck, "
                    "and collaborated with Daniella Reichstetter on a gyrobike FYP."
                ),
            ),
            SearchResult(
                title=f"{name} - Project Notes",
                link=f"https://example.com/{slug}-project",
                snippet=(
                    f"{name} has public project references involving Gyrobike, "
                    "Tuck entrepreneurship, and former classmates."
                ),
            ),
        ]


class SerpApiSearchClient(SearchClient):
    def __init__(self, api_key: str, per_query_limit: int = 3, max_results: int = 6) -> None:
        self.api_key = api_key
        self.per_query_limit = per_query_limit
        self.max_results = max_results

    def search_person(self, name: str, class_year: str) -> list[SearchResult]:
        collected: list[SearchResult] = []
        for query in build_person_queries(name, class_year):
            params = {
                "q": query,
                "api_key": self.api_key,
                "engine": "google",
                "num": self.per_query_limit,
            }
            response = serpapi.GoogleSearch(params).get_dict()
            organic_results = response.get("organic_results", [])
            collected.extend(
                SearchResult(
                    title=item.get("title", ""),
                    link=item.get("link", ""),
                    snippet=item.get("snippet", ""),
                )
                for item in organic_results[: self.per_query_limit]
            )
        return _dedupe_results(collected, self.max_results)


class SerpAPISearchClient(SerpApiSearchClient):
    """Alias with requested naming."""
