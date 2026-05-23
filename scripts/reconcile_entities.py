from __future__ import annotations

import argparse

from backend.config import get_settings
from backend.db.store import Store
from backend.pipeline.reconcile import reconcile_graph


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Reconcile entities and infer graph edges.")
    parser.add_argument(
        "--database-url",
        default=settings.database_url,
        help="Database URL. Defaults to DATABASE_URL from .env.",
    )
    args = parser.parse_args(argv)

    summary = reconcile_graph(Store(args.database_url))
    print(
        "Reconciled "
        f"{summary.entities_consolidated} entities; "
        f"inferred {summary.inferred_connections} connections."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
