from __future__ import annotations

import json
import re

from openai import OpenAI
from pydantic import BaseModel, Field

from backend.db.models import RawPage
from backend.db.store import KEEP_VERDICTS, Store
from backend.pipeline.openai_retry import retry_openai_call
from backend.pricing import estimate_llm_dollars
from backend.resolution.embeddings import (
    DeterministicEmbeddingClient,
    EmbeddingClient,
    OpenAIEmbeddingClient,
)

MAX_DEEP_CONTEXT_CHARS = 200_000
DEEP_SYSTEM_PROMPT = (
    "Answer ONLY from the provided page chunks. Cite the source_url for every claim in "
    "markdown like [source](url). If the chunks don't answer the question, say so."
)


class QueryAnswer(BaseModel):
    answer: str


class QueryExpansion(BaseModel):
    queries: list[str] = Field(default_factory=list)


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
        record_response_usage(
            self.store,
            response,
            model=self.model,
            purpose="strict_query",
        )
        return QueryAnswer(answer=response.output_text.strip())


class MockDeepQueryClient(QueryClient):
    def __init__(self, store: Store) -> None:
        self.store = store

    def answer_question(self, question: str) -> QueryAnswer:
        expanded = local_query_expansions(question)
        chunks = self.store.hybrid_retrieve_chunks(
            query=question,
            expanded_queries=expanded,
            query_embedding=DeterministicEmbeddingClient().embed_text(
                "\n".join([question, *expanded]),
                purpose="query_embedding",
            ),
            limit=20,
        )
        if not chunks:
            return QueryAnswer(answer="The stored raw pages do not answer that question.")
        sources = ", ".join(f"[source]({chunk['source_url']})" for chunk in chunks[:3])
        return QueryAnswer(answer=f"Deep mode reviewed {len(chunks)} chunks: {sources}")


class DeepQueryClient(QueryClient):
    def __init__(
        self,
        *,
        store: Store,
        api_key: str,
        model: str = "gpt-5.5",
        expansion_model: str = "gpt-5.4-mini",
        embedding_client: EmbeddingClient | None = None,
    ) -> None:
        self.store = store
        self.client = OpenAI(api_key=api_key, max_retries=0)
        self.model = model
        self.expansion_model = expansion_model
        self.embedding_client = embedding_client or OpenAIEmbeddingClient(
            api_key=api_key,
            store=store,
        )

    def answer_question(self, question: str) -> QueryAnswer:
        expanded = self.expand_query(question)
        query_embedding = self.embedding_client.embed_text(
            "\n".join([question, *expanded]),
            purpose="query_embedding",
        )
        chunks = self.store.hybrid_retrieve_chunks(
            query=question,
            expanded_queries=expanded,
            query_embedding=query_embedding,
            limit=50,
        )
        context = build_hybrid_context(chunks[:10])
        response = retry_openai_call(
            lambda: self.client.responses.create(
                model=self.model,
                input=[
                    {"role": "system", "content": DEEP_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Question: {question}\n\n"
                            f"Page chunks:\n{context or 'No page chunks found.'}"
                        ),
                    },
                ],
            )
        )
        record_response_usage(
            self.store,
            response,
            model=self.model,
            purpose="research_answer",
        )
        return QueryAnswer(answer=response.output_text.strip())

    def expand_query(self, question: str) -> list[str]:
        response = retry_openai_call(
            lambda: self.client.responses.parse(
                model=self.expansion_model,
                input=[
                    {
                        "role": "system",
                        "content": (
                            "Return 5 to 10 alternate search phrasings and corrected spellings "
                            "for named entities in the user's research question."
                        ),
                    },
                    {"role": "user", "content": question},
                ],
                text_format=QueryExpansion,
            )
        )
        record_response_usage(
            self.store,
            response,
            model=self.expansion_model,
            purpose="query_expansion",
        )
        parsed = response.output_parsed or QueryExpansion()
        return [query.strip() for query in parsed.queries if query.strip()][:10]


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


def build_hybrid_context(chunks: list[dict[str, object]]) -> str:
    output: list[str] = []
    remaining = MAX_DEEP_CONTEXT_CHARS
    for index, chunk in enumerate(chunks, start=1):
        header = (
            f"Chunk {index}\n"
            f"source_url: {chunk['source_url']}\n"
            f"title: {chunk.get('page_title', '')}\n"
            f"score: {chunk.get('score', 0)}\n"
            "text:\n"
        )
        text_value = str(chunk.get("text", ""))
        if remaining <= len(header):
            break
        excerpt = text_value[: remaining - len(header)]
        output.append(f"{header}{excerpt}")
        remaining -= len(header) + len(excerpt)
        if remaining <= 0:
            break
    return "\n\n---\n\n".join(output)


def local_query_expansions(question: str) -> list[str]:
    expansions = [question]
    lower = question.lower()
    if "gyrobyke" in lower:
        expansions.append(re.sub("gyrobyke", "gyrobike", question, flags=re.IGNORECASE))
    if "gyrobike" in lower:
        expansions.append("Gyrobike first-year project")
    return expansions


def record_response_usage(
    store: Store,
    response: object,
    *,
    model: str,
    purpose: str,
) -> None:
    usage = getattr(response, "usage", None)
    prompt_tokens = int(
        getattr(usage, "input_tokens", None) or getattr(usage, "prompt_tokens", None) or 0
    )
    completion_tokens = int(
        getattr(usage, "output_tokens", None) or getattr(usage, "completion_tokens", None) or 0
    )
    store.record_llm_usage(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        dollars=estimate_llm_dollars(
            model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        ),
        purpose=purpose,
    )
