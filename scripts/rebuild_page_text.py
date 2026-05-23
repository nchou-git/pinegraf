from __future__ import annotations

import argparse
import gzip
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from urllib.parse import urlparse

from sqlalchemy import select

from backend.config import get_settings
from backend.db.models import RawPage
from backend.db.store import Store
from backend.pipeline.page_fetcher import TextBoilerplate, build_boilerplate_model, clean_html


@dataclass(frozen=True)
class PageSnapshot:
    raw_page_id: int
    source_url: str
    raw_html: str
    before_text: str

    @property
    def host(self) -> str:
        return urlparse(self.source_url).netloc.lower()


@dataclass(frozen=True)
class RebuildSummary:
    pages_seen: int
    pages_updated: int
    host_models: int
    tuck_before_avg: float
    tuck_after_avg: float

    @property
    def tuck_drop_percent(self) -> float:
        if self.tuck_before_avg <= 0:
            return 0.0
        return ((self.tuck_before_avg - self.tuck_after_avg) / self.tuck_before_avg) * 100


def rebuild_page_text(
    store: Store,
    *,
    dry_run: bool = False,
    snapshots: Sequence[PageSnapshot] | None = None,
) -> RebuildSummary:
    snapshots = list(snapshots or _load_snapshots(store))
    host_texts: dict[str, list[str]] = {}

    for snapshot in snapshots:
        _, text = clean_html(snapshot.raw_html)
        if snapshot.host:
            host_texts.setdefault(snapshot.host, []).append(text)

    models = {host: build_boilerplate_model(texts) for host, texts in host_texts.items()}
    if not dry_run:
        for host, model in models.items():
            store.upsert_host_boilerplate(host=host, prefix=model.prefix, suffix=model.suffix)

    after_text_by_id: dict[int, str] = {}
    snapshots_by_id = {snapshot.raw_page_id: snapshot for snapshot in snapshots}
    pages_updated = 0
    for snapshot in snapshots:
        model = models.get(snapshot.host, TextBoilerplate())
        title, text = clean_html(snapshot.raw_html, boilerplate=model)
        after_text_by_id[snapshot.raw_page_id] = text
        if text == snapshot.before_text:
            continue
        pages_updated += 1
        if not dry_run:
            store.update_raw_page_text(snapshot.raw_page_id, page_title=title, page_text=text)

    tuck_ids = [
        snapshot.raw_page_id for snapshot in snapshots if snapshot.host == "tuck.dartmouth.edu"
    ]
    return RebuildSummary(
        pages_seen=len(snapshots),
        pages_updated=pages_updated,
        host_models=len(models),
        tuck_before_avg=_average(
            len(snapshots_by_id[raw_page_id].before_text) for raw_page_id in tuck_ids
        ),
        tuck_after_avg=_average(len(after_text_by_id[raw_page_id]) for raw_page_id in tuck_ids),
    )


def spot_check_pages(
    store: Store,
    *,
    snapshots: Sequence[PageSnapshot] | None = None,
    limit: int = 5,
) -> list[dict[str, object]]:
    snapshots = list(snapshots or _load_snapshots(store))
    tuck_pages = [snapshot for snapshot in snapshots if snapshot.host == "tuck.dartmouth.edu"]
    if not tuck_pages:
        return []
    indices = _spread_indices(len(tuck_pages), limit)
    rows: list[dict[str, object]] = []
    for index in indices:
        snapshot = tuck_pages[index]
        rows.append(
            {
                "raw_page_id": snapshot.raw_page_id,
                "source_url": snapshot.source_url,
                "before_len": len(snapshot.before_text),
                "after_len": len(_current_page_text(store, snapshot.raw_page_id)),
                "before_start": snapshot.before_text[:220],
                "after_start": _current_page_text(store, snapshot.raw_page_id)[:220],
            }
        )
    return rows


def _load_snapshots(store: Store) -> list[PageSnapshot]:
    with store.session() as session:
        rows = list(
            session.execute(
                select(RawPage.id, RawPage.source_url, RawPage.raw_html_gz, RawPage.page_text)
                .where(RawPage.raw_html_gz.is_not(None))
                .order_by(RawPage.id.asc())
            )
        )
    snapshots: list[PageSnapshot] = []
    for raw_page_id, source_url, raw_html_gz, page_text in rows:
        if raw_html_gz is None:
            continue
        snapshots.append(
            PageSnapshot(
                raw_page_id=raw_page_id,
                source_url=source_url,
                raw_html=gzip.decompress(raw_html_gz).decode("utf-8"),
                before_text=page_text,
            )
        )
    return snapshots


def _current_page_text(store: Store, raw_page_id: int) -> str:
    with store.session() as session:
        page_text = session.execute(
            select(RawPage.page_text).where(RawPage.id == raw_page_id)
        ).scalar_one()
    return page_text


def _average(values: Iterable[int]) -> float:
    lengths = list(values)
    if not lengths:
        return 0.0
    return sum(lengths) / len(lengths)


def _spread_indices(total: int, limit: int) -> list[int]:
    if total <= limit:
        return list(range(total))
    return sorted({round(index * (total - 1) / (limit - 1)) for index in range(limit)})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild raw_pages.page_text from stored HTML.")
    parser.add_argument(
        "--database-url",
        default=get_settings().database_url,
        help="Database URL. Defaults to DATABASE_URL from .env.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Compute without writing.")
    parser.add_argument(
        "--spot-check-limit",
        type=int,
        default=5,
        help="Number of Tuck pages to print before/after snippets for.",
    )
    args = parser.parse_args(argv)

    store = Store(args.database_url)
    snapshots = _load_snapshots(store)
    summary = rebuild_page_text(store, dry_run=args.dry_run, snapshots=snapshots)
    print(
        "Rebuilt page_text for "
        f"{summary.pages_seen} pages; updated {summary.pages_updated}; "
        f"host models {summary.host_models}; "
        f"Tuck avg {summary.tuck_before_avg:.1f} -> {summary.tuck_after_avg:.1f} "
        f"({summary.tuck_drop_percent:.1f}% drop)."
    )
    for row in spot_check_pages(store, snapshots=snapshots, limit=args.spot_check_limit):
        print(
            "\n"
            f"raw_page_id={row['raw_page_id']} before={row['before_len']} "
            f"after={row['after_len']}\n"
            f"url={row['source_url']}\n"
            f"before_start={row['before_start']}\n"
            f"after_start={row['after_start']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
