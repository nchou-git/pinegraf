from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select

from backend.db.models import Source
from backend.db.store import Store
from backend.normalization.normalizer import normalize_fetch
from backend.parse.orchestrator import run_full_parse

LOGGER = logging.getLogger(__name__)
DEMO_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "data" / "demo_fixtures"
MANIFEST_NAME = "manifest.yaml"


@dataclass(frozen=True)
class DemoFixture:
    filename: str
    original_url: str
    source: str
    display_name: str | None = None


async def run_demo_seed_if_needed(
    store: Store,
    *,
    fixture_dir: Path = DEMO_FIXTURE_DIR,
) -> None:
    if _source_count(store) > 0:
        LOGGER.info("demo seed skipped -- sources already exist")
        return

    manifest_path = fixture_dir / MANIFEST_NAME
    fixtures = _read_manifest(manifest_path)
    if not fixtures:
        LOGGER.info("demo seed skipped -- no fixtures in %s", manifest_path)
        return
    available_fixtures: list[DemoFixture] = []
    for fixture in fixtures:
        if (fixture_dir / fixture.filename).is_file():
            available_fixtures.append(fixture)
        else:
            LOGGER.warning("demo seed fixture missing filename=%s", fixture.filename)
    if not available_fixtures:
        LOGGER.info("demo seed skipped -- no fixture files present in %s", fixture_dir)
        return

    LOGGER.info(
        "demo seed starting fixtures=%s manifest=%s",
        len(available_fixtures),
        manifest_path,
    )
    sources = {
        "tuck": store.upsert_source(
            kind="domain",
            identifier="tuck.dartmouth.edu",
            trust_weight=0.9,
            display_name="Tuck demo fixtures",
            notes="Seeded demo fixtures from local HTML files.",
        ),
        "external": store.upsert_source(
            kind="domain",
            identifier="demo-external",
            trust_weight=0.7,
            display_name="External demo fixtures",
            notes="Seeded demo fixtures from local HTML files.",
        ),
    }
    fetch_ids_by_source = {source.id: [] for source in sources.values()}

    for fixture in available_fixtures:
        source_key = "tuck" if fixture.source == "tuck" else "external"
        source = sources[source_key]
        file_path = fixture_dir / fixture.filename

        run = store.create_source_run(
            source_id=source.id,
            kind="sitemap",
            spec={
                "demo_seed": True,
                "fixture": fixture.filename,
                "original_url": fixture.original_url,
            },
            triggered_by="demo_seed",
            status="complete",
        )
        fetch = store.add_fetch(
            source_run_id=run.id,
            url=fixture.original_url,
            body_bytes=file_path.read_bytes(),
            http_status=200,
            content_type="text/html",
            discovery_method="demo_fixture",
        )
        fetch_ids_by_source[source.id].append(fetch.id)
        LOGGER.info(
            "demo seed fetch added source=%s filename=%s url=%s",
            source.identifier,
            fixture.filename,
            fixture.original_url,
        )

    for fetch_ids in fetch_ids_by_source.values():
        for fetch_id in fetch_ids:
            await normalize_fetch(fetch_id, store=store)
            LOGGER.info("demo seed fetch normalized fetch_id=%s", fetch_id)

    for source in sources.values():
        fetch_ids = fetch_ids_by_source[source.id]
        if not fetch_ids:
            continue
        parse_run = store.create_source_run(
            source_id=source.id,
            kind="parse",
            spec={
                "demo_seed": True,
                "scope": "fetch_ids",
                "fetch_ids": [str(item) for item in fetch_ids],
            },
            triggered_by="demo_seed",
            status="running",
        )
        await run_full_parse(
            source.id,
            store=store,
            progress_run_id=parse_run.id,
            scope="fetch_ids",
            fetch_ids=fetch_ids,
        )
        store.update_source_run(parse_run.id, status="complete", finished=True)
        store.refresh_source_crawl_counters(source.id)
        store.mark_source_full_recrawl_complete(source.id)
        LOGGER.info(
            "demo seed parsed source=%s fetches=%s parse_run_id=%s",
            source.identifier,
            len(fetch_ids),
            parse_run.id,
        )

    LOGGER.info("demo seed complete")


def _source_count(store: Store) -> int:
    with store.session() as session:
        return int(session.execute(select(func.count()).select_from(Source)).scalar_one())


def _read_manifest(path: Path) -> list[DemoFixture]:
    if not path.is_file():
        return []
    fixtures: list[DemoFixture] = []
    current: dict[str, str] | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line == "fixtures:":
            continue
        if line.startswith("- "):
            if current is not None:
                fixtures.append(_fixture_from_row(current))
            current = {}
            line = line[2:].strip()
        if ":" not in line or current is None:
            continue
        key, value = line.split(":", 1)
        current[key.strip()] = _unquote(value.strip())
    if current is not None:
        fixtures.append(_fixture_from_row(current))
    return fixtures


def _fixture_from_row(row: dict[str, str]) -> DemoFixture:
    filename = row.get("filename", "").strip()
    original_url = row.get("original_url", "").strip()
    source = row.get("source", "external").strip().casefold()
    display_name = row.get("display_name", "").strip() or None
    if not filename or not original_url:
        raise ValueError("demo fixture manifest entries require filename and original_url")
    return DemoFixture(
        filename=filename,
        original_url=original_url,
        source="tuck" if source == "tuck" else "external",
        display_name=display_name,
    )


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
