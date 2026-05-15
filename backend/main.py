from __future__ import annotations

import csv
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.config import get_settings
from backend.db.store import Store
from backend.pipeline.extract import ExtractClient
from backend.pipeline.extract import MockExtractClient
from backend.pipeline.extract import OpenAIExtractClient
from backend.pipeline.query import MockQueryClient
from backend.pipeline.query import OpenAIQueryClient
from backend.pipeline.query import QueryClient
from backend.pipeline.search import MockSearchClient
from backend.pipeline.search import SearchClient
from backend.pipeline.search import SerpAPISearchClient


class QueryRequest(BaseModel):
    question: str


def load_alumni_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [{"name": row["name"], "class_year": row["class_year"]} for row in reader]


def build_clients() -> tuple[SearchClient, ExtractClient, QueryClient]:
    settings = get_settings()
    search: SearchClient = (
        MockSearchClient()
        if settings.use_mock_search
        else SerpAPISearchClient(api_key=settings.serpapi_api_key)
    )
    extract: ExtractClient = (
        MockExtractClient()
        if settings.use_mock_extract
        else OpenAIExtractClient(api_key=settings.openai_api_key)
    )
    query: QueryClient = (
        MockQueryClient()
        if settings.use_mock_query
        else OpenAIQueryClient(api_key=settings.openai_api_key)
    )
    return search, extract, query


app = FastAPI(title="TuckScout")
settings = get_settings()
store = Store(settings.database_url)
store.init_db()
search_client, extract_client, query_client = build_clients()


@app.post("/enrich")
def enrich() -> dict[str, object]:
    alumni = load_alumni_csv(Path("data/alumni.csv"))
    enriched_count = 0

    for record in alumni:
        search_results = search_client.search_person(record["name"], record["class_year"])
        profile = extract_client.extract_profile(record["name"], search_results)
        store.upsert_profile(
            name=profile.name,
            class_year=record["class_year"],
            current_company=profile.current_company,
            current_title=profile.current_title,
            past_companies=profile.past_companies,
        )
        enriched_count += 1

    return {"status": "ok", "enriched_count": enriched_count}


@app.post("/query")
def query(payload: QueryRequest) -> dict[str, str]:
    profiles = [
        {
            "name": p.name,
            "class_year": p.class_year,
            "current_company": p.current_company,
            "current_title": p.current_title,
            "past_companies": p.past_companies,
        }
        for p in store.list_profiles()
    ]
    answer = query_client.answer_question(payload.question, profiles)
    return {"answer": answer.answer}


app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
