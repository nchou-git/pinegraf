from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend import demo_seed
from backend.config import get_settings
from backend.db.models import Fetch, Source, SourceRun


@pytest.mark.asyncio
async def test_demo_seed_loads_fixture_manifest(store, tmp_path, monkeypatch) -> None:
    (tmp_path / "sarah.html").write_text(
        "<html><body>Sarah Ketterer founded Causeway Capital.</body></html>",
        encoding="utf-8",
    )
    (tmp_path / "daniella.html").write_text(
        "<html><body>Daniella Reichstetter works at Tuck.</body></html>",
        encoding="utf-8",
    )
    (tmp_path / "manifest.yaml").write_text(
        """
fixtures:
  - filename: sarah.html
    original_url: https://tuck.dartmouth.edu/mba/alumni-stories/sarah-ketterer
    source: tuck
    display_name: Sarah Ketterer
  - filename: daniella.html
    original_url: https://www.vcic.org/daniella-reichstetter-tuck-center-for-entrepreneurship/
    source: external
""".strip(),
        encoding="utf-8",
    )
    normalized: list[uuid.UUID] = []
    parsed: list[tuple[uuid.UUID, list[uuid.UUID]]] = []

    async def fake_normalize_fetch(fetch_id, *, store):
        del store
        normalized.append(uuid.UUID(str(fetch_id)))
        return uuid.uuid4()

    async def fake_run_full_parse(source_id, *, store, progress_run_id, scope, fetch_ids):
        del store, progress_run_id, scope
        parsed.append((uuid.UUID(str(source_id)), [uuid.UUID(str(item)) for item in fetch_ids]))
        return set()

    monkeypatch.setattr(demo_seed, "normalize_fetch", fake_normalize_fetch)
    monkeypatch.setattr(demo_seed, "run_full_parse", fake_run_full_parse)

    await demo_seed.run_demo_seed_if_needed(store, fixture_dir=tmp_path)

    with store.session() as session:
        sources = session.execute(select(Source).order_by(Source.identifier)).scalars().all()
        fetches = session.execute(select(Fetch).order_by(Fetch.url)).scalars().all()
        runs = session.execute(select(SourceRun)).scalars().all()

    assert [source.identifier for source in sources] == ["demo-external", "tuck.dartmouth.edu"]
    assert len(fetches) == 2
    assert {fetch.content_type for fetch in fetches} == {"text/html"}
    assert len([run for run in runs if run.kind == "sitemap" and run.status == "complete"]) == 2
    assert len([run for run in runs if run.kind == "parse" and run.status == "complete"]) == 2
    assert len(normalized) == 2
    assert len(parsed) == 2

    await demo_seed.run_demo_seed_if_needed(store, fixture_dir=tmp_path)
    with store.session() as session:
        assert len(session.execute(select(Fetch)).scalars().all()) == 2


def test_app_startup_does_not_seed_when_demo_mode_disabled(store, monkeypatch) -> None:
    from backend import main as main_module

    called = []

    async def fake_seed(store):
        called.append(store)

    monkeypatch.delenv("PINEGRAF_DEMO_MODE", raising=False)
    get_settings.cache_clear()
    monkeypatch.setattr(main_module, "run_demo_seed_if_needed", fake_seed)

    with TestClient(main_module.create_app(store)) as client:
        assert client.get("/health").status_code == 200

    assert called == []
