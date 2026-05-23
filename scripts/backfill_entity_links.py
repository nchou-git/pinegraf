from __future__ import annotations

import argparse

from backend.config import get_settings
from backend.db.store import Store


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Backfill entity_id on facts/connections from AlumniProfile links."
    )
    parser.add_argument(
        "--database-url",
        default=settings.database_url,
        help="Database URL. Defaults to DATABASE_URL from .env.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes. Defaults to dry-run.",
    )
    args = parser.parse_args(argv)

    summary = Store(args.database_url).backfill_entity_links(dry_run=not args.apply)
    mode = "applied" if args.apply else "dry-run"
    print(f"Backfill entity links ({mode}): {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
