from __future__ import annotations

from sqlalchemy import inspect

from backend.db.models import Base
from backend.db.store import SCHEMA_TABLES, Store


def test_test_database_has_foundation_schema(store: Store) -> None:
    inspector = inspect(store.engine)
    table_names = set(inspector.get_table_names())
    assert set(SCHEMA_TABLES).issubset(table_names)

    for table_name in SCHEMA_TABLES:
        expected = {column.name for column in Base.metadata.tables[table_name].columns}
        actual = {column["name"] for column in inspector.get_columns(table_name)}
        assert expected <= actual
