from __future__ import annotations

import json

from openai import OpenAI
from pydantic import BaseModel

from backend.pipeline.openai_retry import retry_openai_call


class QueryAnswer(BaseModel):
    answer: str


class QueryClient:
    def answer_question(self, question: str, database: dict[str, object]) -> QueryAnswer:
        raise NotImplementedError


class MockQueryClient(QueryClient):
    def answer_question(self, question: str, database: dict[str, object]) -> QueryAnswer:
        profiles = _profiles_from_database(database)
        connections = database.get("connections", []) if isinstance(database, dict) else []
        q = question.lower()
        if "acme" in q:
            matches = [p["name"] for p in profiles if p.get("current_company") == "Acme Corp"]
            return QueryAnswer(answer=f"Acme alumni: {', '.join(matches) if matches else 'none'}")
        if "connection" in q or "worked with" in q or "gyrobike" in q:
            summaries = [
                (f"{c.get('alum_name')} -> {c.get('connected_name')}: {c.get('context', '')}")
                for c in connections
                if isinstance(c, dict)
            ]
            return QueryAnswer(
                answer=(
                    f"Known connections: {'; '.join(summaries) if summaries else 'none stored yet'}"
                )
            )
        return QueryAnswer(answer=f"Loaded {len(profiles)} alumni profiles. Question: {question}")


class OpenAIQueryClient(QueryClient):
    def __init__(self, api_key: str, model: str = "gpt-5.5") -> None:
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def answer_question(self, question: str, database: dict[str, object]) -> QueryAnswer:
        payload = json.dumps(database, indent=2, default=str)
        prompt = (
            "You answer questions strictly from the provided alumni database records. "
            "The records include profiles, facts, connections, and projects. Surface "
            "non-obvious relationships when the stored evidence supports them, cite names "
            "and source URLs when available, and say briefly when data is missing.\n\n"
            f"Question: {question}\n\nDatabase records:\n{payload}"
        )
        response = retry_openai_call(
            lambda: self.client.responses.create(model=self.model, input=prompt)
        )
        return QueryAnswer(answer=response.output_text.strip())


def _profiles_from_database(
    database: dict[str, object] | list[dict[str, object]],
) -> list[dict[str, object]]:
    if isinstance(database, list):
        return database
    profiles = database.get("profiles", [])
    if isinstance(profiles, list):
        return [p for p in profiles if isinstance(p, dict)]
    return []
