from __future__ import annotations

import json

from openai import OpenAI
from pydantic import BaseModel

from backend.db.models import RawPage
from backend.db.store import KEEP_VERDICTS, Store
from backend.pipeline.openai_retry import retry_openai_call

MAX_DEEP_CONTEXT_CHARS = 200_000
DEEP_SYSTEM_PROMPT = (
    "Answer ONLY from the provided page excerpts. After each claim cite the source_url in "
    "markdown like [source](url). If the pages don't answer the question, say so."
)


class QueryAnswer(BaseModel):
    answer: str


class QueryClient:
    def answer_question(self, question: str) -> QueryAnswer:
        raise NotImplementedError


class MockQueryClient(QueryClient):
    def __init__(self, store: Store) -> None:
        self.store = store

    def answer_question(self, question: str) -> QueryAnswer:
        database = self.store.database_context(verdicts=KEEP_VERDICTS)
        profiles = [p for p in database["profiles"] if isinstance(p, dict)]
        connections = [c for c in database["connections"] if isinstance(c, dict)]
        q = question.lower()
        if "acme" in q:
            matches = [
                str(profile["name"])
                for profile in profiles
                if profile.get("current_company") == "Acme Corp"
            ]
            return QueryAnswer(answer=f"Acme alumni: {', '.join(matches) if matches else 'none'}")
        if "connection" in q or "worked with" in q or "gyrobike" in q:
            summaries = [
                (
                    f"{connection.get('alum_name')} -> "
                    f"{connection.get('connected_name')}: {connection.get('context', '')}"
                )
                for connection in connections
            ]
            return QueryAnswer(
                answer=(
                    f"Known connections: {'; '.join(summaries) if summaries else 'none stored yet'}"
                )
            )
        return QueryAnswer(answer=f"Loaded {len(profiles)} alumni profiles. Question: {question}")


class OpenAIQueryClient(QueryClient):
    def __init__(self, *, store: Store, api_key: str, model: str = "gpt-5.4-mini") -> None:
        self.store = store
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def answer_question(self, question: str) -> QueryAnswer:
        database = self.store.database_context(verdicts=KEEP_VERDICTS)
        payload = json.dumps(database, indent=2, default=str)
        prompt = (
            "You answer questions strictly from structured alumni database records. "
            "The records include profiles, facts, connections, and projects. Use only "
            "rows with validation_verdict='keep'.\n"
            "Each profile may include a structured positions list with entries like "
            "company, title, location, start_date, end_date, position_type, is_current, "
            "source_url, and merge_group_id.\n"
            "An alumnus can have multiple concurrent current positions (for example board + "
            "day job, or advisor + employment). Do not assume only one current role.\n"
            "Positions sharing the same merge_group_id are the same role observed in multiple "
            "sources; collapse those duplicates when presenting results, but you can rely on "
            "any one of them as evidence.\n"
            "For current-role or current-company questions, prefer structured positions over "
            "older profile.current_company/profile.current_title strings because positions are "
            "more detailed and source-attributed.\n"
            "Surface relationships when the stored evidence supports them, cite source URLs "
            "when available, and say when the database does not contain enough information.\n\n"
            f"Question: {question}\n\nDatabase records:\n{payload}"
        )
        response = retry_openai_call(
            lambda: self.client.responses.create(model=self.model, input=prompt)
        )
        return QueryAnswer(answer=response.output_text.strip())


class MockDeepQueryClient(QueryClient):
    def __init__(self, store: Store) -> None:
        self.store = store

    def answer_question(self, question: str) -> QueryAnswer:
        pages = self.store.raw_pages_fts_search(question, limit=20)
        if not pages:
            return QueryAnswer(answer="The stored raw pages do not answer that question.")
        sources = ", ".join(f"[source]({page.source_url})" for page in pages[:3])
        return QueryAnswer(answer=f"Deep mode reviewed {len(pages)} raw pages: {sources}")


class DeepQueryClient(QueryClient):
    def __init__(self, *, store: Store, api_key: str, model: str = "gpt-5.5") -> None:
        self.store = store
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def answer_question(self, question: str) -> QueryAnswer:
        pages = self.store.raw_pages_fts_search(question, limit=20)
        context = build_deep_context(pages)
        response = retry_openai_call(
            lambda: self.client.responses.create(
                model=self.model,
                input=[
                    {"role": "system", "content": DEEP_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Question: {question}\n\n"
                            f"Page excerpts:\n{context or 'No page excerpts found.'}"
                        ),
                    },
                ],
            )
        )
        return QueryAnswer(answer=response.output_text.strip())


def build_deep_context(pages: list[RawPage]) -> str:
    chunks: list[str] = []
    remaining = MAX_DEEP_CONTEXT_CHARS
    for index, page in enumerate(pages, start=1):
        header = (
            f"Source {index}\n"
            f"alum_name: {page.alum_name}\n"
            f"source_url: {page.source_url}\n"
            f"title: {page.page_title}\n"
            "text:\n"
        )
        if remaining <= len(header):
            break
        excerpt = page.page_text[: remaining - len(header)]
        chunks.append(f"{header}{excerpt}")
        remaining -= len(header) + len(excerpt)
        if remaining <= 0:
            break
    return "\n\n---\n\n".join(chunks)
