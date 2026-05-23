from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

load_dotenv()


def _csv(name: str) -> list[str]:
    """Read a comma-separated env var into a list of stripped strings."""
    raw = os.getenv(name, "")
    return [s.strip() for s in raw.split(",") if s.strip()]


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # External APIs
    openai_api_key: str = Field(default="")

    # Database
    # Prefer Postgres in .env, e.g.:
    #   postgresql+psycopg://pinegraf:pinegraf@localhost:5432/pinegraf
    # Store.init_db falls back to SQLite for local dev if Postgres is unavailable.
    database_url: str = Field(default="sqlite:///./pinegraf.db")

    # Admin auth
    pinegraf_admin_password: str = Field(default="pinegraf")
    pinegraf_admin_cookie_secret: str = Field(default="dev-secret")

    # Mock toggles for offline dev / tests
    use_mock_extract: bool = Field(default=True)
    use_mock_query: bool = Field(default=True)
    use_mock_fetch: bool = Field(default=True)

    # Crawler config
    crawl_seed_urls: list[str] = Field(default_factory=list)
    crawl_sitemap_urls: list[str] = Field(default_factory=list)
    crawl_allowed_domains: list[str] = Field(default_factory=list)
    crawl_max_pages: int = Field(default=500, ge=1)
    crawl_max_depth: int = Field(default=2, ge=0)

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
            database_url=os.getenv("DATABASE_URL", "sqlite:///./pinegraf.db"),
            pinegraf_admin_password=os.getenv("PINEGRAF_ADMIN_PASSWORD", "pinegraf"),
            pinegraf_admin_cookie_secret=os.getenv("PINEGRAF_ADMIN_COOKIE_SECRET", "dev-secret"),
            use_mock_extract=os.getenv("USE_MOCK_EXTRACT", "true"),
            use_mock_query=os.getenv("USE_MOCK_QUERY", "true"),
            use_mock_fetch=os.getenv("USE_MOCK_FETCH", "true"),
            crawl_seed_urls=_csv("CRAWL_SEED_URLS"),
            crawl_sitemap_urls=_csv("CRAWL_SITEMAP_URLS"),
            crawl_allowed_domains=_csv("CRAWL_ALLOWED_DOMAINS"),
            crawl_max_pages=int(os.getenv("CRAWL_MAX_PAGES", "500")),
            crawl_max_depth=int(os.getenv("CRAWL_MAX_DEPTH", "2")),
        )
    except ValidationError as exc:
        raise RuntimeError(f"Invalid configuration: {exc}") from exc
