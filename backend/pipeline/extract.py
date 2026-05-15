from __future__ import annotations

from pydantic import BaseModel
from pydantic import Field
from openai import OpenAI

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


class OpenAIExtractClient(ExtractClient):
    def __init__(self, api_key: str, model: str = "gpt-5.3") -> None:
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def extract_profile(self, name: str, results: list[SearchResult]) -> ExtractedProfile:
        context = "\n".join(
            [f"Title: {r.title}\nSnippet: {r.snippet}\nLink: {r.link}" for r in results]
        )
        prompt = (
            "Extract structured alumni profile JSON with keys: "
            "name, current_company, current_title, past_companies (array).\n"
            f"Person name: {name}\n"
            f"Search context:\n{context}"
        )

        response = self.client.responses.create(
            model=self.model,
            input=prompt,
            text={"format": {"type": "json_schema", "name": "profile", "schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "current_company": {"type": "string"},
                    "current_title": {"type": "string"},
                    "past_companies": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "current_company", "current_title", "past_companies"],
                "additionalProperties": False,
            }}},
        )
        output_text = response.output_text
        return ExtractedProfile.model_validate_json(output_text)
