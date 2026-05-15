from __future__ import annotations

from pydantic import BaseModel
from openai import OpenAI


class QueryAnswer(BaseModel):
    answer: str


class QueryClient:
    def answer_question(self, question: str, profiles: list[dict[str, object]]) -> QueryAnswer:
        raise NotImplementedError


class MockQueryClient(QueryClient):
    def answer_question(self, question: str, profiles: list[dict[str, object]]) -> QueryAnswer:
        q = question.lower()
        if "acme" in q:
            matches = [p["name"] for p in profiles if p.get("current_company") == "Acme Corp"]
            return QueryAnswer(answer=f"Acme alumni: {', '.join(matches) if matches else 'none'}")
        return QueryAnswer(answer=f"Loaded {len(profiles)} alumni profiles. Question: {question}")


class OpenAIQueryClient(QueryClient):
    def __init__(self, api_key: str, model: str = "gpt-5.3-mini") -> None:
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def answer_question(self, question: str, profiles: list[dict[str, object]]) -> QueryAnswer:
        prompt = (
            "You answer questions strictly from the provided alumni database records. "
            "If data is missing, say so briefly.\n\n"
            f"Question: {question}\n\nProfiles:\n{profiles}"
        )
        response = self.client.responses.create(model=self.model, input=prompt)
        return QueryAnswer(answer=response.output_text.strip())
