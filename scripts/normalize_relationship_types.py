from __future__ import annotations

import argparse

from backend.config import get_settings
from backend.db.models import Connection
from backend.db.store import Store
from backend.pipeline.relationship_types import normalize_relationship_type


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Normalize existing connection relationship types."
    )
    parser.add_argument(
        "--database-url",
        default=settings.database_url,
        help="Database URL. Defaults to DATABASE_URL from .env.",
    )
    parser.add_argument("--apply", action="store_true", help="Write changes. Defaults to dry-run.")
    args = parser.parse_args(argv)

    store = Store(args.database_url)
    changed = 0
    with store.session() as session:
        rows = list(session.query(Connection).order_by(Connection.id.asc()))
        for row in rows:
            normalized = normalize_relationship_type(row.relationship_type)
            if row.relationship_type == normalized.relationship_type and (
                not normalized.derivation or normalized.derivation in row.derivation
            ):
                continue
            changed += 1
            if not args.apply:
                continue
            existing = row.derivation.strip()
            incoming = normalized.derivation.strip()
            row.relationship_type = normalized.relationship_type
            if incoming and incoming not in existing:
                row.derivation = f"{existing}; {incoming}" if existing else incoming
        if args.apply:
            session.commit()
    mode = "applied" if args.apply else "dry-run"
    print(f"Normalize relationship types ({mode}): changed={changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
