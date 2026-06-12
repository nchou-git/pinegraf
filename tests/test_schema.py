from __future__ import annotations

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command
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


def test_source_crawl_depth_migration_round_trips(test_database_url: str) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", test_database_url)

    try:
        command.downgrade(config, "0026_doc_embeddings_claim_docs")
        downgraded_engine = create_engine(test_database_url)
        try:
            downgraded = inspect(downgraded_engine)
            assert "crawl_depth" not in {
                column["name"] for column in downgraded.get_columns("sources")
            }
        finally:
            downgraded_engine.dispose()

        command.upgrade(config, "head")
        upgraded_engine = create_engine(test_database_url)
        try:
            upgraded = inspect(upgraded_engine)
            columns = {column["name"] for column in upgraded.get_columns("sources")}
            constraints = {
                constraint["name"] for constraint in upgraded.get_check_constraints("sources")
            }
            assert "crawl_depth" in columns
            assert "ck_sources_crawl_depth" in constraints
        finally:
            upgraded_engine.dispose()
    finally:
        command.upgrade(config, "head")
