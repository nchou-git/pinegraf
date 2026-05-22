from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

load_dotenv()

def _csv(name: str) -> list[str]:
    v = os.getenv(name, "")
    return [s.strip() for s in v.split(",") if s.strip()]


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
    crawl_seed_urls: list[str] = Field(default_factory=list)
    crawl_sitemap_urls: list[str] = Field(default_factory=list)
    crawl_allowed_domains: list[str] = Field(default_factory=list)
    crawl_max_pages: int = Field(default=500)
    crawl_max_depth: int = Field(default=2)

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
            crawl_seed_urls=_csv("CRAWL_SEED_URLS"),
            crawl_sitemap_urls=_csv("CRAWL_SITEMAP_URLS"),
            crawl_allowed_domains=_csv("CRAWL_ALLOWED_DOMAINS"),
            crawl_max_pages=int(os.getenv("CRAWL_MAX_PAGES", "500")),
            crawl_max_depth=int(os.getenv("CRAWL_MAX_DEPTH", "2")),
        )
    except ValidationError as exc:
        raise RuntimeError(f"Invalid configuration: {exc}") from exc
