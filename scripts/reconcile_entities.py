from __future__ import annotations

import argparse

from backend.config import get_settings
from backend.db.store import Store
from backend.resolution.entity_resolver import reconcile_all


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Reconcile entities and infer graph edges.")
    parser.add_argument(
        "--database-url",
        default=settings.database_url,
        help="Database URL. Defaults to DATABASE_URL from .env.",
    )
    args = parser.parse_args(argv)

    result = reconcile_all(Store(args.database_url))
    print(
        "Reconciled "
        f"{result.merged} entities; "
        f"linked {result.linked} explicit targets; "
        f"inferred {result.inferred} connections."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
