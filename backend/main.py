from __future__ import annotations

import asyncio
import csv
import json
import queue
import threading
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel

from backend.config import get_settings
from backend.db.store import Store
from backend.pipeline.extract import (
    ExtractClient,
    MockExtractClient,
    OpenAIExtractClient,
)
from backend.pipeline.query import (
    MockQueryClient,
    OpenAIQueryClient,
    QueryClient,
)
from backend.pipeline.research import (
    EntityExtractor,
    MockEntityExtractor,
    MockPageFetcher,
    MockProfileSynthesizer,
    PageFetcher,
    ProfileSynthesizer,
    ProgressEvent,
    ResearchOrchestrator,
)
from backend.pipeline.search import (
    MockSearchClient,
    SearchClient,
    SerpAPISearchClient,
)


class QueryRequest(BaseModel):
    question: str


def load_alumni_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        records: list[dict[str, str]] = []
        for row in reader:
            name = row["name"].strip()
            class_year = row["class_year"].strip()
            if not name or name.lower() == "name":
                continue
            records.append({"name": name, "class_year": class_year})
        return records


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


def build_research_components() -> tuple[PageFetcher, EntityExtractor, ProfileSynthesizer]:
    settings = get_settings()
    fetcher: PageFetcher = MockPageFetcher() if settings.use_mock_fetch else PageFetcher()
    extractor: EntityExtractor = (
        MockEntityExtractor()
        if settings.use_mock_extract
        else EntityExtractor(api_key=settings.openai_api_key, model="gpt-5.4-mini")
    )
    synthesizer: ProfileSynthesizer = (
        MockProfileSynthesizer()
        if settings.use_mock_extract
        else ProfileSynthesizer(api_key=settings.openai_api_key, model="gpt-5.4")
    )
    return fetcher, extractor, synthesizer


app = FastAPI(title="Pinegraf")
settings = get_settings()
store = Store(settings.database_url)
store.init_db()
search_client, extract_client, query_client = build_clients()


@app.post("/enrich")
async def enrich() -> dict[str, object]:
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


@app.get("/alumni-count")
async def alumni_count() -> dict[str, int]:
    alumni = load_alumni_csv(Path("data/alumni.csv"))
    return {"count": len(alumni)}


@app.get("/research/stream")
async def research_stream() -> StreamingResponse:
    alumni = load_alumni_csv(Path("data/alumni.csv"))
    fetcher, extractor, synthesizer = build_research_components()
    orchestrator = ResearchOrchestrator(
        store=store,
        search_client=search_client,
        fetcher=fetcher,
        extractor=extractor,
        synthesizer=synthesizer,
        max_depth=0,
        pages_per_alum=5,
    )

    event_queue: queue.Queue[ProgressEvent | str] = queue.Queue()
    done_sentinel = "__done__"

    def emit(ev: ProgressEvent) -> None:
        event_queue.put(ev)

    def worker() -> None:
        try:
            orchestrator.run(alumni, emit)
        except Exception as exc:
            event_queue.put(ProgressEvent("done", {"error": f"{type(exc).__name__}: {exc}"}))
        finally:
            fetcher.close()
            event_queue.put(done_sentinel)

    threading.Thread(target=worker, daemon=True, name="pinegraf-research").start()

    async def event_generator() -> AsyncIterator[bytes]:
        while True:
            try:
                ev = event_queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.05)
                continue
            if ev == done_sentinel:
                break
            payload = json.dumps({"kind": ev.kind, **ev.data})
            yield f"data: {payload}\n\n".encode("utf-8")

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/profiles")
async def list_profiles() -> dict[str, object]:
    return {
        "profiles": [
            {
                "name": p.name,
                "class_year": p.class_year,
                "current_company": p.current_company,
                "current_title": p.current_title,
                "past_companies": p.past_companies,
                "education": p.education,
                "bio_summary": p.bio_summary,
                "depth": p.depth,
                "discovered_via": p.discovered_via,
                "last_researched_at": (
                    p.last_researched_at.isoformat() if p.last_researched_at else None
                ),
            }
            for p in store.list_profiles()
        ]
    }


@app.get("/connections")
async def list_connections() -> dict[str, object]:
    return {
        "connections": [
            {
                "alum_name": c.alum_name,
                "connected_name": c.connected_name,
                "context": c.context,
                "source_url": c.source_url,
                "relationship_type": c.relationship_type,
            }
            for c in store.list_connections()
        ]
    }


@app.get("/projects")
async def list_projects() -> dict[str, object]:
    return {
        "projects": [
            {
                "alum_name": p.alum_name,
                "project_name": p.project_name,
                "description": p.description,
                "source_url": p.source_url,
            }
            for p in store.list_projects()
        ]
    }


def database_context() -> dict[str, object]:
    return {
        "profiles": [
            {
                "name": p.name,
                "class_year": p.class_year,
                "current_company": p.current_company,
                "current_title": p.current_title,
                "past_companies": p.past_companies,
                "education": p.education,
                "bio_summary": p.bio_summary,
                "depth": p.depth,
                "discovered_via": p.discovered_via,
            }
            for p in store.list_profiles()
        ],
        "facts": [
            {
                "alum_name": f.alum_name,
                "category": f.category,
                "content": f.content,
                "source_url": f.source_url,
                "confidence": f.confidence,
            }
            for f in store.list_facts()
        ],
        "connections": [
            {
                "alum_name": c.alum_name,
                "connected_name": c.connected_name,
                "context": c.context,
                "source_url": c.source_url,
                "relationship_type": c.relationship_type,
            }
            for c in store.list_connections()
        ],
        "projects": [
            {
                "alum_name": p.alum_name,
                "project_name": p.project_name,
                "description": p.description,
                "source_url": p.source_url,
            }
            for p in store.list_projects()
        ],
    }


@app.post("/query")
async def query(payload: QueryRequest) -> dict[str, str]:
    answer = query_client.answer_question(payload.question, database_context())
    return {"answer": answer.answer}


@app.get("/")
async def frontend_index() -> HTMLResponse:
    return HTMLResponse(Path("frontend/index.html").read_text(encoding="utf-8"))


@app.get("/app.js")
async def frontend_app() -> Response:
    return Response(
        Path("frontend/app.js").read_text(encoding="utf-8"),
        media_type="application/javascript",
    )


@app.get("/favicon.svg")
async def frontend_favicon() -> Response:
    return Response(
        Path("frontend/favicon.svg").read_text(encoding="utf-8"),
        media_type="image/svg+xml",
    )
