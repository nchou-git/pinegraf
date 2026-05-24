from __future__ import annotations

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command
from backend.db.models import Base
from backend.db.store import SCHEMA_TABLES


def test_migration_creates_foundation_schema(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite:///{tmp_path / 'schema.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")

    engine = create_engine(database_url, future=True)
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    assert set(SCHEMA_TABLES).issubset(table_names)

    for table_name in SCHEMA_TABLES:
        expected = {column.name for column in Base.metadata.tables[table_name].columns}
        actual = {column["name"] for column in inspector.get_columns(table_name)}
        assert expected <= actual
