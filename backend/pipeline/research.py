from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from openai import OpenAI
from pydantic import BaseModel, Field

from backend.pipeline.openai_retry import is_insufficient_quota_error, retry_openai_call
from backend.pipeline.search import SearchClient, SearchResult

MAX_PAGE_CHARS = 6000
FETCH_TIMEOUT = 15.0
FETCH_DELAY_SECONDS = 2.0
FETCH_RETRY_BACKOFF_SECONDS = (1.0, 3.0)
USER_AGENT = "PinegrafBot/0.1 (research; contact: nchou-git)"
SKIP_FETCH_DOMAINS = {"linkedin.com", "www.linkedin.com"}
MAX_CONSECUTIVE_INSUFFICIENT_QUOTA_ERRORS = 5


@dataclass
class FetchedPage:
    url: str
    title: str
    text: str


class ExtractedConnection(BaseModel):
    name: str
    context: str = ""
    relationship_type: str = "associate"
    class_year: str | None = None
    source_url: str = ""


class ExtractedProject(BaseModel):
    name: str
    description: str = ""
    source_url: str = ""


class ExtractedFact(BaseModel):
    category: str
    content: str
    confidence: str = "low"
    source_url: str = ""


class PageExtraction(BaseModel):
    current_company: str | None = None
    current_title: str | None = None
    past_companies: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    bio_summary: str = ""
    connections: list[ExtractedConnection] = Field(default_factory=list)
    projects: list[ExtractedProject] = Field(default_factory=list)
    facts: list[ExtractedFact] = Field(default_factory=list)


class SynthesizedProfile(BaseModel):
    current_company: str = ""
    current_title: str = ""
    past_companies: list[str] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    bio_summary: str = ""
    connections: list[ExtractedConnection] = Field(default_factory=list)
    projects: list[ExtractedProject] = Field(default_factory=list)
    facts: list[ExtractedFact] = Field(default_factory=list)


def tag_source_urls(extraction: PageExtraction | SynthesizedProfile, source_url: str) -> None:
    for item in [*extraction.connections, *extraction.projects, *extraction.facts]:
        if not item.source_url:
            item.source_url = source_url


def should_fetch_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    blocked = any(host == domain or host.endswith(f".{domain}") for domain in SKIP_FETCH_DOMAINS)
    return bool(parsed.scheme in {"http", "https"} and not blocked)


class PageFetcher:
    def __init__(
        self,
        delay: float = FETCH_DELAY_SECONDS,
        retries: int = 2,
        retry_backoff_seconds: Sequence[float] | None = None,
    ) -> None:
        self.delay = delay
        self.retry_backoff_seconds = tuple(
            retry_backoff_seconds
            if retry_backoff_seconds is not None
            else FETCH_RETRY_BACKOFF_SECONDS[:retries]
        )
        self.retries = len(self.retry_backoff_seconds)
        self._client = httpx.Client(
            timeout=FETCH_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )

    def fetch(self, url: str) -> FetchedPage | None:
        if not should_fetch_url(url):
            return None
        for attempt in range(len(self.retry_backoff_seconds) + 1):
            try:
                resp = self._client.get(url)
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")
                if "html" not in content_type and "text" not in content_type:
                    return None
                soup = BeautifulSoup(resp.text, "lxml")
                noisy_tags = ["script", "style", "nav", "footer", "header", "noscript", "aside"]
                for tag in soup(noisy_tags):
                    tag.decompose()
                title = (soup.title.string or "").strip() if soup.title else ""
                text = soup.get_text(separator=" ", strip=True)[:MAX_PAGE_CHARS]
                return FetchedPage(url=url, title=title, text=text)
            except httpx.HTTPError:
                if attempt >= len(self.retry_backoff_seconds):
                    return None
                time.sleep(self.retry_backoff_seconds[attempt])
            except Exception:
                return None
            finally:
                if self.delay:
                    time.sleep(self.delay)
        return None

    def close(self) -> None:
        self._client.close()


class MockPageFetcher(PageFetcher):
    def __init__(self) -> None:
        self.delay = 0.0
        self.retries = 0
        self.retry_backoff_seconds = ()

    def fetch(self, url: str) -> FetchedPage | None:
        if not url:
            return None
        slug = url.rstrip("/").split("/")[-1]
        title = slug.replace("-", " ").title()
        text = (
            f"{title}. The alumnus is a Senior Manager at Acme Corp. "
            "Previously worked at Beta Inc and Gamma LLC. Dartmouth Tuck MBA. "
            "A project archive says Errik Anderson and Daniella Reichstetter worked together "
            "on the Gyrobike first-year project, sometimes described as a gyrobike FYP. "
            "The page also mentions company bios, startup work, and classmate connections."
        )
        return FetchedPage(url=url, title=title, text=text)

    def close(self) -> None:
        return None


class EntityExtractor:
    def __init__(self, api_key: str, model: str = "gpt-5.4-mini") -> None:
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def extract(self, alum_name: str, page: FetchedPage) -> PageExtraction:
        response = retry_openai_call(
            lambda: self.client.responses.parse(
                model=self.model,
                input=[
                    {
                        "role": "system",
                        "content": (
                            "Extract structured information about the named alumnus from this "
                            "page. Only include facts supported by the page. Only include facts "
                            "directly stated or strongly implied by this page. Do not infer from "
                            "outside knowledge. For connections, only list real people associated "
                            "with the alumnus (colleagues, co-founders, classmates, project "
                            "partners), not the alumnus themself. For connections: list named "
                            "people who appear in a professional or educational context alongside "
                            "the alumnus. This includes: co-workers, classmates, co-founders, "
                            "board co-members, project collaborators, mentors, mentees, or people "
                            "the alumnus is described as working/serving with. Use the context "
                            "field to capture how they're related. Skip generic mentions (e.g. "
                            "someone quoted in the same article about an unrelated topic). When "
                            "in doubt, include it with a brief context note and 'low' confidence. "
                            "If the page appears to be about a "
                            "different person with the same name as the alumnus (different "
                            "industry, era, or biography), return all fields empty and add a "
                            "single fact with category='disambiguation_warning' and content "
                            "describing why. If the page contains no relevant information about "
                            "this alumnus, return empty fields. Do NOT fabricate to fill the "
                            "schema. Mark fact confidence as 'low' by default. Upgrade to "
                            "'medium' only with clear explicit evidence. Upgrade to 'high' only "
                            "for direct quotes or official sources. Put the page URL in "
                            "source_url for every extracted fact, connection, and project."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Alumnus: {alum_name}\n"
                            f"Page URL: {page.url}\n"
                            f"Page title: {page.title}\n\n"
                            f"Page text:\n{page.text}"
                        ),
                    },
                ],
                text_format=PageExtraction,
            )
        )
        extraction = response.output_parsed or PageExtraction()
        tag_source_urls(extraction, page.url)
        return extraction


class MockEntityExtractor(EntityExtractor):
    def __init__(self) -> None:
        self.model = "mock"

    def extract(self, alum_name: str, page: FetchedPage) -> PageExtraction:
        lower = page.text.lower()
        connections: list[ExtractedConnection] = []
        projects: list[ExtractedProject] = []
        facts = [
            ExtractedFact(
                category="career",
                content=f"{alum_name} is described as a Senior Manager at Acme Corp.",
                confidence="high",
                source_url=page.url,
            ),
            ExtractedFact(
                category="career",
                content=f"{alum_name} previously worked at Beta Inc and Gamma LLC.",
                confidence="medium",
                source_url=page.url,
            ),
        ]
        if "gyrobike" in lower:
            connected_name = "Daniella Reichstetter"
            if alum_name.lower() == connected_name.lower():
                connected_name = "Errik Anderson"
            connections.append(
                ExtractedConnection(
                    name=connected_name,
                    context="Worked together on the Gyrobike first-year project at Tuck.",
                    relationship_type="project collaborator",
                    source_url=page.url,
                )
            )
            projects.append(
                ExtractedProject(
                    name="Gyrobike FYP",
                    description="Tuck first-year project connected to gyrobike work.",
                    source_url=page.url,
                )
            )
            facts.append(
                ExtractedFact(
                    category="project",
                    content=(
                        f"{alum_name} has a Tuck project reference involving the Gyrobike FYP."
                    ),
                    confidence="medium",
                    source_url=page.url,
                )
            )
        return PageExtraction(
            current_company="Acme Corp",
            current_title="Senior Manager",
            past_companies=["Beta Inc", "Gamma LLC"],
            education=["Dartmouth Tuck MBA"],
            bio_summary=(
                f"{alum_name} is a Tuck alumnus with operating and project experience. "
                "Stored mock evidence links the person to Acme Corp and Tuck project work."
            ),
            connections=connections,
            projects=projects,
            facts=facts,
        )


class ProfileSynthesizer:
    def __init__(self, api_key: str, model: str = "gpt-5.4") -> None:
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def synthesize(
        self, alum_name: str, class_year: str, extractions: list[PageExtraction]
    ) -> SynthesizedProfile:
        if not extractions:
            return SynthesizedProfile()
        payload = json.dumps([e.model_dump() for e in extractions], indent=2, default=str)
        response = retry_openai_call(
            lambda: self.client.responses.parse(
                model=self.model,
                input=[
                    {
                        "role": "system",
                        "content": (
                            "Reconcile per-page extractions into one canonical alumnus profile. "
                            "Deduplicate companies, connections, and projects. Prefer recent, "
                            "specific information. If two per-page extracts disagree on a field, "
                            "prefer the most recent/authoritative source. If sources are equally "
                            "authoritative and disagree, set the field to empty rather than "
                            "guessing. Drop unsupported data or same-name noise. Drop any "
                            "connection that appears in only one extract unless the context "
                            "explicitly supports it. Drop facts that don't have a source_url. Do "
                            "not introduce new facts during synthesis. Only reconcile what the "
                            "per-page extracts already produced. Keep bio_summary to 2-3 "
                            "sentences. Preserve source_url values on facts, connections, and "
                            "projects."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Alumnus: {alum_name}\n"
                            f"Class year: {class_year}\n\n"
                            f"Per-page extractions:\n{payload}"
                        ),
                    },
                ],
                text_format=SynthesizedProfile,
            )
        )
        profile = response.output_parsed or SynthesizedProfile()
        profile.facts = [fact for fact in profile.facts if fact.source_url.strip()]
        return profile


class MockProfileSynthesizer(ProfileSynthesizer):
    def __init__(self) -> None:
        self.model = "mock"

    def synthesize(
        self, alum_name: str, class_year: str, extractions: list[PageExtraction]
    ) -> SynthesizedProfile:
        del class_year
        if not extractions:
            return SynthesizedProfile(
                bio_summary=f"No fetched public pages produced structured evidence for {alum_name}."
            )
        return SynthesizedProfile(
            current_company=next(
                (item.current_company or "" for item in extractions if item.current_company),
                "",
            ),
            current_title=next(
                (item.current_title or "" for item in extractions if item.current_title),
                "",
            ),
            past_companies=dedupe_strings(
                company for item in extractions for company in item.past_companies
            ),
            education=dedupe_strings(school for item in extractions for school in item.education),
            bio_summary=next(
                (item.bio_summary for item in extractions if item.bio_summary),
                f"{alum_name} has stored public-page evidence from the research crawl.",
            ),
            connections=dedupe_connections(
                connection for item in extractions for connection in item.connections
            ),
            projects=dedupe_projects(project for item in extractions for project in item.projects),
            facts=dedupe_facts(fact for item in extractions for fact in item.facts),
        )


def dedupe_strings(values: Iterator[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        cleaned = value.strip()
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            output.append(cleaned)
    return output


def dedupe_connections(values: Iterator[ExtractedConnection]) -> list[ExtractedConnection]:
    seen: set[tuple[str, str]] = set()
    output: list[ExtractedConnection] = []
    for value in values:
        key = (value.name.strip().lower(), value.context.strip().lower())
        if value.name.strip() and key not in seen:
            seen.add(key)
            output.append(value)
    return output


def dedupe_projects(values: Iterator[ExtractedProject]) -> list[ExtractedProject]:
    seen: set[str] = set()
    output: list[ExtractedProject] = []
    for value in values:
        key = value.name.strip().lower()
        if value.name.strip() and key not in seen:
            seen.add(key)
            output.append(value)
    return output


def dedupe_facts(values: Iterator[ExtractedFact]) -> list[ExtractedFact]:
    seen: set[tuple[str, str]] = set()
    output: list[ExtractedFact] = []
    for value in values:
        key = (value.category.strip().lower(), value.content.strip().lower())
        if value.content.strip() and key not in seen:
            seen.add(key)
            output.append(value)
    return output


@dataclass
class ProgressEvent:
    kind: str  # "class_start" | "alum_start" | "stage" | "alum_done" | "class_done" | "done"
    data: dict


class OpenAICircuitBreakerHalt(RuntimeError):
    pass


class ResearchOrchestrator:
    def __init__(
        self,
        store,
        search_client: SearchClient,
        fetcher: PageFetcher,
        extractor: EntityExtractor,
        synthesizer: ProfileSynthesizer,
        max_depth: int = 2,
        pages_per_alum: int = 4,
    ) -> None:
        self.store = store
        self.search = search_client
        self.fetcher = fetcher
        self.extractor = extractor
        self.synthesizer = synthesizer
        self.max_depth = max_depth
        self.pages_per_alum = pages_per_alum
        self.consecutive_insufficient_quota_errors = 0

    def _record_openai_success(self) -> None:
        self.consecutive_insufficient_quota_errors = 0

    def _record_openai_error(self, exc: Exception) -> None:
        if not is_insufficient_quota_error(exc):
            self.consecutive_insufficient_quota_errors = 0
            return
        self.consecutive_insufficient_quota_errors += 1
        if self.consecutive_insufficient_quota_errors >= MAX_CONSECUTIVE_INSUFFICIENT_QUOTA_ERRORS:
            raise OpenAICircuitBreakerHalt(
                "OpenAI insufficient_quota circuit breaker tripped after "
                f"{MAX_CONSECUTIVE_INSUFFICIENT_QUOTA_ERRORS} consecutive failures"
            ) from exc

    def _research_one(
        self,
        name: str,
        class_year: str,
        depth: int,
        emit: Callable[[ProgressEvent], None],
    ) -> list[str]:
        emit(ProgressEvent("stage", {"name": name, "stage": "searching"}))
        results: list[SearchResult] = self.search.search_person(name, class_year)[
            : self.pages_per_alum
        ]

        extractions: list[PageExtraction] = []
        for idx, r in enumerate(results, start=1):
            emit(
                ProgressEvent(
                    "stage",
                    {
                        "name": name,
                        "stage": f"fetching {idx}/{len(results)}",
                        "url": r.link,
                    },
                )
            )
            page = self.fetcher.fetch(r.link)
            if not page or not page.text:
                emit(
                    ProgressEvent(
                        "stage",
                        {
                            "name": name,
                            "stage": f"fetch_skipped_or_failed {idx}/{len(results)}",
                            "url": r.link,
                        },
                    )
                )
                continue
            emit(
                ProgressEvent(
                    "stage",
                    {
                        "name": name,
                        "stage": f"extracting {idx}/{len(results)}",
                    },
                )
            )
            try:
                ext = self.extractor.extract(name, page)
                self._record_openai_success()
                for c in ext.connections:
                    c.context = c.context or page.title
                tag_source_urls(ext, page.url)
                extractions.append(ext)
            except Exception as exc:
                try:
                    self._record_openai_error(exc)
                except OpenAICircuitBreakerHalt as halt:
                    emit(
                        ProgressEvent(
                            "stage",
                            {
                                "name": name,
                                "stage": f"openai_circuit_breaker: {halt}",
                            },
                        )
                    )
                    raise
                emit(
                    ProgressEvent(
                        "stage",
                        {
                            "name": name,
                            "stage": f"extract_error: {type(exc).__name__}: {exc}",
                        },
                    )
                )

        emit(ProgressEvent("stage", {"name": name, "stage": "synthesizing"}))
        try:
            profile = self.synthesizer.synthesize(name, class_year, extractions)
            if extractions:
                self._record_openai_success()
        except Exception as exc:
            try:
                self._record_openai_error(exc)
            except OpenAICircuitBreakerHalt as halt:
                emit(
                    ProgressEvent(
                        "stage",
                        {
                            "name": name,
                            "stage": f"openai_circuit_breaker: {halt}",
                        },
                    )
                )
                raise
            emit(
                ProgressEvent(
                    "stage",
                    {
                        "name": name,
                        "stage": f"synth_error: {type(exc).__name__}: {exc}",
                    },
                )
            )
            profile = SynthesizedProfile()

        self.store.upsert_profile(
            name=name,
            class_year=class_year,
            current_company=profile.current_company,
            current_title=profile.current_title,
            past_companies=profile.past_companies,
            education=profile.education,
            bio_summary=profile.bio_summary,
            depth=depth,
        )
        self.store.add_facts(
            name,
            [
                {
                    "category": f.category,
                    "content": f.content,
                    "source_url": f.source_url,
                    "confidence": f.confidence,
                }
                for f in profile.facts
            ],
        )
        new_names = self.store.add_connections(
            name,
            [
                {
                    "name": c.name,
                    "context": c.context,
                    "source_url": c.source_url,
                    "relationship_type": c.relationship_type,
                }
                for c in profile.connections
            ],
        )
        self.store.add_projects(
            name,
            [
                {
                    "name": p.name,
                    "description": p.description,
                    "source_url": p.source_url,
                }
                for p in profile.projects
            ],
        )
        enqueued: list[str] = []
        if depth < self.max_depth:
            connection_by_name = {c.name: c for c in profile.connections}
            for new_name in new_names:
                if new_name.lower() == name.lower():
                    continue
                connection = connection_by_name.get(new_name)
                discovered_class_year = (
                    connection.class_year if connection and connection.class_year else class_year
                )
                was_enqueued = self.store.enqueue_crawl(
                    name=new_name,
                    class_year=discovered_class_year,
                    depth=depth + 1,
                    discovered_via=name,
                )
                if was_enqueued:
                    enqueued.append(new_name)
                    emit(
                        ProgressEvent(
                            "discovered",
                            {
                                "name": new_name,
                                "class_year": discovered_class_year,
                                "depth": depth + 1,
                                "discovered_via": name,
                                "overall_done": self.store.count_crawl_done(),
                                "overall_total": self.store.count_crawl(),
                            },
                        )
                    )
        crawl_status = "done" if extractions else "partial"
        self.store.mark_crawl_status(name, crawl_status, class_year=class_year)
        return enqueued

    def run(
        self,
        seed_alumni: list[dict],
        emit: Callable[[ProgressEvent], None],
    ) -> None:
        # Seed the queue
        for a in seed_alumni:
            self.store.enqueue_crawl(
                name=a["name"],
                class_year=a["class_year"],
                depth=0,
                discovered_via="seed",
            )

        # Order classes by appearance in CSV (Tuck first, then T27, T14, ...)
        seen_classes: list[str] = []
        for a in seed_alumni:
            if a["class_year"] not in seen_classes:
                seen_classes.append(a["class_year"])
        # Also include any class_years from discovered people that don't match CSV order
        for cy in self.store.distinct_class_years_pending():
            if cy not in seen_classes:
                seen_classes.append(cy)

        class_index = 0
        while class_index < len(seen_classes):
            class_year = seen_classes[class_index]
            class_index += 1
            pending = self.store.list_pending_by_class(class_year)
            if not pending:
                continue
            emit(
                ProgressEvent(
                    "class_start",
                    {
                        "class_year": class_year,
                        "count": self.store.count_crawl_by_class(class_year),
                        "done": self.store.count_crawl_done_by_class(class_year),
                        "overall_total": self.store.count_crawl(),
                        "overall_done": self.store.count_crawl_done(),
                    },
                )
            )
            done_in_class = self.store.count_crawl_done_by_class(class_year)
            # Process until this class's queue is exhausted (handles new ones enqueued mid-loop)
            while True:
                batch = self.store.list_pending_by_class(class_year)
                if not batch:
                    break
                for item in batch:
                    self.store.mark_crawl_status(item.name, "running", class_year=item.class_year)
                    emit(
                        ProgressEvent(
                            "alum_start",
                            {
                                "name": item.name,
                                "class_year": item.class_year,
                                "depth": item.depth,
                                "discovered_via": item.discovered_via,
                            },
                        )
                    )
                    try:
                        self._research_one(item.name, item.class_year, item.depth, emit)
                    except OpenAICircuitBreakerHalt as exc:
                        self.store.mark_crawl_status(
                            item.name, "partial", class_year=item.class_year
                        )
                        emit(
                            ProgressEvent(
                                "stage",
                                {
                                    "name": item.name,
                                    "stage": f"research_halted: {type(exc).__name__}: {exc}",
                                },
                            )
                        )
                        raise
                    except Exception as exc:
                        self.store.mark_crawl_status(
                            item.name, "failed", class_year=item.class_year
                        )
                        emit(
                            ProgressEvent(
                                "stage",
                                {
                                    "name": item.name,
                                    "stage": f"research_error: {type(exc).__name__}: {exc}",
                                },
                            )
                        )
                    done_in_class = self.store.count_crawl_done_by_class(class_year)
                    emit(
                        ProgressEvent(
                            "alum_done",
                            {
                                "name": item.name,
                                "class_year": item.class_year,
                                "done_in_class": done_in_class,
                                "total_in_class": self.store.count_crawl_by_class(class_year),
                                "overall_done": self.store.count_crawl_done(),
                                "overall_total": self.store.count_crawl(),
                            },
                        )
                    )
                for cy in self.store.distinct_class_years_pending():
                    if cy not in seen_classes:
                        seen_classes.append(cy)
            emit(
                ProgressEvent(
                    "class_done",
                    {
                        "class_year": class_year,
                        "total_done": done_in_class,
                        "total_in_class": self.store.count_crawl_by_class(class_year),
                        "overall_done": self.store.count_crawl_done(),
                        "overall_total": self.store.count_crawl(),
                    },
                )
            )

        emit(
            ProgressEvent(
                "done",
                {
                    "overall_done": self.store.count_crawl_done(),
                    "overall_total": self.store.count_crawl(),
                },
            )
        )
