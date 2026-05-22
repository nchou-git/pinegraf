from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

load_dotenv()


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    openai_api_key: str = Field(default="")
    pinegraf_admin_password: str = Field(default="admin")
    pinegraf_admin_cookie_secret: str = Field(default="dev-secret")
    # Prefer Postgres in .env:
    # postgresql+psycopg://pinegraf:pinegraf@localhost:5432/pinegraf
    # Store.init_db falls back to sqlite:///./pinegraf.db for local dev if Postgres is unavailable.
    database_url: str = Field(default="sqlite:///./pinegraf.db")
    use_mock_extract: bool = Field(default=True)
    use_mock_query: bool = Field(default=True)
    use_mock_fetch: bool = Field(default=True)

    @field_validator(
        "use_mock_extract",
        "use_mock_query",
        "use_mock_fetch",
        mode="before",
    )
    @classmethod
    def parse_bool(cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in {"1", "true", "yes", "on"}
        return bool(value)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    try:
        return Settings(
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            pinegraf_admin_password=os.getenv("PINEGRAF_ADMIN_PASSWORD", "admin"),
            pinegraf_admin_cookie_secret=os.getenv(
                "PINEGRAF_ADMIN_COOKIE_SECRET",
                "dev-secret",
            ),
            database_url=os.getenv("DATABASE_URL", "sqlite:///./pinegraf.db"),
            use_mock_extract=os.getenv("USE_MOCK_EXTRACT", "true"),
            use_mock_query=os.getenv("USE_MOCK_QUERY", "true"),
            use_mock_fetch=os.getenv("USE_MOCK_FETCH", "true"),
        )
    except ValidationError as exc:
        raise RuntimeError(f"Invalid configuration: {exc}") from exc
