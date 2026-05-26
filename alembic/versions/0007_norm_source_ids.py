"""normalize source identifiers

Revision ID: 0007_norm_source_ids
Revises: 0006_source_respect_robots
Create Date: 2026-05-26 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlparse

import sqlalchemy as sa

from alembic import op

revision: str = "0007_norm_source_ids"
down_revision: str | None = "0006_source_respect_robots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    sources = sa.table(
        "sources",
        sa.column("id", sa.Uuid()),
        sa.column("kind", sa.Text()),
        sa.column("identifier", sa.Text()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    source_runs = sa.table(
        "source_runs",
        sa.column("source_id", sa.Uuid()),
    )

    rows = list(
        connection.execute(
            sa.select(
                sources.c.id,
                sources.c.kind,
                sources.c.identifier,
                sources.c.created_at,
                sa.func.count(source_runs.c.source_id).label("run_count"),
            )
            .select_from(sources.outerjoin(source_runs, source_runs.c.source_id == sources.c.id))
            .group_by(sources.c.id, sources.c.kind, sources.c.identifier, sources.c.created_at)
        ).mappings()
    )
    groups: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        normalized = _normalize_identifier(str(row["kind"]), str(row["identifier"]))
        if not normalized:
            continue
        groups.setdefault(f"{row['kind']}:{normalized}", []).append(
            {**row, "normalized": normalized}
        )

    for group in groups.values():
        group.sort(
            key=lambda row: (
                -int(row["run_count"]),
                row["created_at"],
                str(row["id"]),
            )
        )
        survivor = group[0]
        losers = group[1:]
        for loser in losers:
            connection.execute(
                sa.update(source_runs)
                .where(source_runs.c.source_id == loser["id"])
                .values(source_id=survivor["id"])
            )
        if losers:
            connection.execute(
                sa.delete(sources).where(sources.c.id.in_([loser["id"] for loser in losers]))
            )
        connection.execute(
            sa.update(sources)
            .where(sources.c.id == survivor["id"])
            .values(identifier=survivor["normalized"])
        )


def downgrade() -> None:
    pass


def _normalize_identifier(kind: str, raw: str) -> str:
    value = str(raw or "").strip()
    if kind == "file":
        return value
    if kind != "domain":
        return value
    if "://" not in value:
        value = f"//{value}"
    parsed = urlparse(value)
    host = (parsed.netloc or parsed.path.split("/", 1)[0]).lower().strip()
    if "@" in host:
        host = host.rsplit("@", 1)[-1]
    host = host.split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    return host.rstrip("/")
