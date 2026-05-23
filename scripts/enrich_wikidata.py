from __future__ import annotations

import argparse

from backend.config import get_settings
from backend.db.store import Store
from backend.sources.wikidata import SparqlWikidataSource, enrich_wikidata


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Enrich Pinegraf entities from Wikidata.")
    parser.add_argument(
        "--database-url",
        default=settings.database_url,
        help="Database URL. Defaults to DATABASE_URL from .env.",
    )
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    source = SparqlWikidataSource()
    try:
        summary = enrich_wikidata(Store(args.database_url), source=source, limit=args.limit)
    finally:
        source.close()
    print(
        "Wikidata enrichment saw "
        f"{summary.entities_seen} entities, matched {summary.entities_matched}, "
        f"wrote {summary.attributes_written} attributes."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
