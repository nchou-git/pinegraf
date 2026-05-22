from __future__ import annotations

from alembic.config import Config

from alembic import command


def test_entity_migration_round_trips_on_sqlite(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'migration.db'}")
    config = Config("alembic.ini")

    command.upgrade(config, "head")
    command.downgrade(config, "-1")
    command.upgrade(config, "head")
