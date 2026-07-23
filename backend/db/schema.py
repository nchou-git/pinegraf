from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

from backend.db.models import Base


def create_fresh_schema(engine: Engine) -> None:
    """Create the current model schema on an empty database.

    This intentionally does not run the historical Alembic chain. The AWS test
    service starts from a fresh Postgres database, so the authoritative schema is
    the current SQLAlchemy model metadata plus required Postgres extensions.
    """

    with engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    Base.metadata.create_all(engine)
