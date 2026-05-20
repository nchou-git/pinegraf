from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import get_settings  # noqa: E402
from backend.db.models import Connection, Fact, Project, RawPage  # noqa: E402
from backend.db.store import Store  # noqa: E402
from backend.pipeline.parser import (  # noqa: E402
    ExtractedConnection,
    ExtractedFact,
    ExtractedProject,
    OpenAIValidationClient,
    PageExtraction,
    ValidationClient,
    apply_validation,
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


def validate_project(
    validator: ValidationClient,
    raw_page: RawPage,
    project: Project,
) -> bool:
    extraction = PageExtraction(
        projects=[
            ExtractedProject(
                project_name=project.project_name,
                description=project.description,
                validation_verdict=project.validation_verdict,
            )
        ]
    )
    apply_validation(extraction, validator.validate(raw_page, extraction))
    return bool(extraction.projects and extraction.projects[0].validation_verdict != "drop")


def validate_connection(
    validator: ValidationClient,
    raw_page: RawPage,
    connection: Connection,
) -> bool:
    extraction = PageExtraction(
        connections=[
            ExtractedConnection(
                connected_name=connection.connected_name,
                context=connection.context,
                relationship_type=connection.relationship_type,
                validation_verdict=connection.validation_verdict,
            )
        ]
    )
    apply_validation(extraction, validator.validate(raw_page, extraction))
    return bool(extraction.connections and extraction.connections[0].validation_verdict != "drop")


def validate_fact(
    validator: ValidationClient,
    raw_page: RawPage,
    fact: Fact,
) -> bool:
    extraction = PageExtraction(
        facts=[
            ExtractedFact(
                category=fact.category,
                content=fact.content,
                confidence=fact.confidence,
                validation_verdict=fact.validation_verdict,
            )
        ]
    )
    apply_validation(extraction, validator.validate(raw_page, extraction))
    return bool(extraction.facts and extraction.facts[0].validation_verdict != "drop")


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
    validator = OpenAIValidationClient(api_key=settings.openai_api_key, model="gpt-5.4-mini")
    dropped = 0
    kept = 0
    missing_source_items = 0

    for project in store.list_projects():
        if project.raw_page is None:
            kept += 1
            missing_source_items += 1
            continue
        if validate_project(validator, project.raw_page, project):
            kept += 1
            continue
        dropped += 1
        message = (
            f"{'DRY RUN ' if args.dry_run else ''}drop project id={project.id} "
            f"alum={project.alum_name!r} project={project.project_name!r} "
            f"source={project.raw_page.source_url!r}"
        )
        print(message)
        log_drop(message)
        if not args.dry_run:
            store.delete_project(project.id)

    for connection in store.list_connections():
        if connection.raw_page is None:
            kept += 1
            missing_source_items += 1
            continue
        if validate_connection(validator, connection.raw_page, connection):
            kept += 1
            continue
        dropped += 1
        message = (
            f"{'DRY RUN ' if args.dry_run else ''}drop connection id={connection.id} "
            f"alum={connection.alum_name!r} connected={connection.connected_name!r} "
            f"source={connection.raw_page.source_url!r}"
        )
        print(message)
        log_drop(message)
        if not args.dry_run:
            store.delete_connection(connection.id)

    for fact in store.list_facts():
        if fact.raw_page is None:
            kept += 1
            missing_source_items += 1
            continue
        if validate_fact(validator, fact.raw_page, fact):
            kept += 1
            continue
        dropped += 1
        message = (
            f"{'DRY RUN ' if args.dry_run else ''}drop fact id={fact.id} "
            f"alum={fact.alum_name!r} category={fact.category!r} "
            f"source={fact.raw_page.source_url!r}"
        )
        print(message)
        log_drop(message)
        if not args.dry_run:
            store.delete_fact(fact.id)

    summary = f"{dropped} items dropped, {kept} kept"
    if missing_source_items:
        summary = f"{summary}, {missing_source_items} items missing source raw pages kept"
    if args.dry_run:
        summary = f"dry run: {summary}"
    print(summary)
    print(f"drop log: {LOG_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
