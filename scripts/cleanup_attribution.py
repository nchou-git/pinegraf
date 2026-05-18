from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import get_settings  # noqa: E402
from backend.db.models import Connection, Fact, Project  # noqa: E402
from backend.db.store import Store  # noqa: E402
from backend.pipeline.research import (  # noqa: E402
    AttributionValidator,
    ExtractedConnection,
    ExtractedFact,
    ExtractedProject,
    FetchedPage,
    PageExtraction,
    PageFetcher,
)

LOG_PATH = Path(__file__).with_name("cleanup_attribution.log")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Remove misattributed extracted items.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print and log rows that would be dropped without deleting them.",
    )
    return parser.parse_args()


def fetch_source(
    fetcher: PageFetcher,
    cache: dict[str, FetchedPage | None],
    source_url: str,
) -> FetchedPage | None:
    if not source_url:
        return None
    if source_url not in cache:
        cache[source_url] = fetcher.fetch(source_url)
    return cache[source_url]


def validate_project(
    validator: AttributionValidator,
    page: FetchedPage,
    project: Project,
) -> bool:
    extraction = PageExtraction(
        projects=[
            ExtractedProject(
                name=project.project_name,
                description=project.description,
                source_url=project.source_url,
            )
        ]
    )
    validated = validator.validate(project.alum_name, page.text, extraction)
    return bool(validated.projects)


def validate_connection(
    validator: AttributionValidator,
    page: FetchedPage,
    connection: Connection,
) -> bool:
    extraction = PageExtraction(
        connections=[
            ExtractedConnection(
                name=connection.connected_name,
                context=connection.context,
                relationship_type=connection.relationship_type,
                source_url=connection.source_url,
            )
        ]
    )
    validated = validator.validate(connection.alum_name, page.text, extraction)
    return bool(validated.connections)


def validate_fact(
    validator: AttributionValidator,
    page: FetchedPage,
    fact: Fact,
) -> bool:
    extraction = PageExtraction(
        facts=[
            ExtractedFact(
                category=fact.category,
                content=fact.content,
                confidence=fact.confidence,
                source_url=fact.source_url,
            )
        ]
    )
    validated = validator.validate(fact.alum_name, page.text, extraction)
    return bool(validated.facts)


def drop_reasons(validator: AttributionValidator) -> str:
    reasons = [
        f"{drop.category}[{drop.index}] {drop.item}: {drop.reason}" for drop in validator.last_drops
    ]
    return "; ".join(reasons) or "validator did not keep item"


def log_drop(message: str) -> None:
    with LOG_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(f"{message}\n")


def main() -> int:
    args = parse_args()
    settings = get_settings()
    if not settings.openai_api_key:
        print("OPENAI_API_KEY is required to run cleanup attribution validation.")
        return 1

    LOG_PATH.write_text("", encoding="utf-8")
    store = Store(settings.database_url)
    store.init_db()
    fetcher = PageFetcher(delay=2.0)
    validator = AttributionValidator(api_key=settings.openai_api_key, model="gpt-5.4-mini")
    page_cache: dict[str, FetchedPage | None] = {}
    unreachable_sources: set[str] = set()
    missing_source_items = 0
    dropped = 0
    kept = 0

    try:
        for project in store.list_projects():
            page = fetch_source(fetcher, page_cache, project.source_url)
            if page is None or not page.text:
                kept += 1
                if project.source_url:
                    unreachable_sources.add(project.source_url)
                else:
                    missing_source_items += 1
                continue
            if validate_project(validator, page, project):
                kept += 1
                continue
            dropped += 1
            message = (
                f"{'DRY RUN ' if args.dry_run else ''}drop project id={project.id} "
                f"alum={project.alum_name!r} project={project.project_name!r} "
                f"source={project.source_url!r}: {drop_reasons(validator)}"
            )
            print(message)
            log_drop(message)
            if not args.dry_run:
                store.delete_project(project.id)

        for connection in store.list_connections():
            page = fetch_source(fetcher, page_cache, connection.source_url)
            if page is None or not page.text:
                kept += 1
                if connection.source_url:
                    unreachable_sources.add(connection.source_url)
                else:
                    missing_source_items += 1
                continue
            if validate_connection(validator, page, connection):
                kept += 1
                continue
            dropped += 1
            message = (
                f"{'DRY RUN ' if args.dry_run else ''}drop connection id={connection.id} "
                f"alum={connection.alum_name!r} connected={connection.connected_name!r} "
                f"source={connection.source_url!r}: {drop_reasons(validator)}"
            )
            print(message)
            log_drop(message)
            if not args.dry_run:
                store.delete_connection(connection.id)

        for fact in store.list_facts():
            page = fetch_source(fetcher, page_cache, fact.source_url)
            if page is None or not page.text:
                kept += 1
                if fact.source_url:
                    unreachable_sources.add(fact.source_url)
                else:
                    missing_source_items += 1
                continue
            if validate_fact(validator, page, fact):
                kept += 1
                continue
            dropped += 1
            message = (
                f"{'DRY RUN ' if args.dry_run else ''}drop fact id={fact.id} "
                f"alum={fact.alum_name!r} category={fact.category!r} "
                f"source={fact.source_url!r}: {drop_reasons(validator)}"
            )
            print(message)
            log_drop(message)
            if not args.dry_run:
                store.delete_fact(fact.id)
    finally:
        fetcher.close()

    summary = (
        f"{dropped} items dropped, {kept} kept, {len(unreachable_sources)} source URLs unreachable"
    )
    if missing_source_items:
        summary = f"{summary}, {missing_source_items} items missing source URLs kept"
    if args.dry_run:
        summary = f"dry run: {summary}"
    print(summary)
    print(f"drop log: {LOG_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
