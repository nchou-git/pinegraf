from __future__ import annotations

import openai
from pydantic import BaseModel, Field

from backend.pipeline.openai_retry import retry_openai_call
from backend.pipeline.search import SearchResult


class ExtractedProfile(BaseModel):
    name: str
    current_company: str
    current_title: str
    past_companies: list[str] = Field(default_factory=list)


class ExtractClient:
    def extract_profile(self, name: str, results: list[SearchResult]) -> ExtractedProfile:
        raise NotImplementedError


class MockExtractClient(ExtractClient):
    def extract_profile(self, name: str, results: list[SearchResult]) -> ExtractedProfile:
        snippet = results[0].snippet if results else ""
        company = "Acme Corp" if "Acme" in snippet else "Unknown Co"
        title = "Senior Manager" if "Senior Manager" in snippet else "Unknown Title"
        return ExtractedProfile(
            name=name,
            current_company=company,
            current_title=title,
            past_companies=["Beta Inc", "Gamma LLC"],
        )


class OpenAIExtractedProfile(BaseModel):
    current_company: str | None
    current_title: str | None
    past_companies: list[str] | None


def _clean_string(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _clean_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [cleaned for item in value if (cleaned := _clean_string(item))]


class OpenAIExtractClient(ExtractClient):
    def __init__(self, api_key: str, model: str = "gpt-5.5") -> None:
        self.client = openai.OpenAI(api_key=api_key)
        self.model = model

    def extract_profile(self, name: str, results: list[SearchResult]) -> ExtractedProfile:
        context = "\n\n".join(
            [
                "\n".join(
                    [
                        f"Result {index}",
                        f"Title: {_clean_string(result.title)}",
                        f"Snippet: {_clean_string(result.snippet)}",
                        f"Link: {_clean_string(result.link)}",
                    ]
                )
                for index, result in enumerate(results, start=1)
            ]
        )

        response = retry_openai_call(
            lambda: self.client.responses.parse(
                model=self.model,
                input=[
                    {
                        "role": "system",
                        "content": (
                            "Extract an alumni career profile from public search-result text. "
                            "Use only the supplied evidence. If a string field is unknown, "
                            "return null. If no past companies are supported, return an empty list."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Alumnus name: {name}\n\n"
                            f"Search results:\n{context or 'No search results provided.'}"
                        ),
                    },
                ],
                text_format=OpenAIExtractedProfile,
            )
        )
        parsed = response.output_parsed

        return ExtractedProfile(
            name=name,
            current_company=_clean_string(getattr(parsed, "current_company", None)),
            current_title=_clean_string(getattr(parsed, "current_title", None)),
            past_companies=_clean_string_list(getattr(parsed, "past_companies", None)),
        )
