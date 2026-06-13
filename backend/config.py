from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

load_dotenv()


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    database_url: str
    openai_api_key: str = Field(default="")
    pdl_api_key: str = Field(default="")

    pinegraf_admin_password: str

    admin_session_secret: str
    admin_session_max_age_seconds: int = Field(default=28800, ge=60)
    secure_cookies: bool = Field(default=True)

    pinegraf_contact: str = Field(default="ops@example.com")
    max_pages: int = Field(default=10000, ge=1)
    crawl_concurrency: int = Field(default=10, ge=1)
    crawl_liveness_check_interval: int = Field(default=25, ge=1)
    recrawl_default_days: int = Field(default=7, ge=1)
    snippet_max_chars: int = Field(default=400, ge=120)
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)
    db_pool_recycle_seconds: int = Field(default=1800, ge=30)
    db_pool_pre_ping: bool = Field(default=True)
    use_mock_embeddings: bool = Field(default=False)
    demo_mode: bool = Field(default=False)
    extraction_model: str = Field(default="gpt-5.4-mini")

    workspace_display_name: str = Field(default="Tuck School of Business")
    workspace_slug: str = Field(default="tuck")

    uploads_dir: str = Field(default="/tmp/pinegraf_uploads")

    @field_validator("use_mock_embeddings", "secure_cookies", "demo_mode", mode="before")
    @classmethod
    def parse_bool(_cls, value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in {"1", "true", "yes", "on"}
        return bool(value)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    try:
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise RuntimeError("DATABASE_URL is required")
        admin_secret = os.getenv("ADMIN_SESSION_SECRET")
        if not admin_secret:
            raise RuntimeError("ADMIN_SESSION_SECRET is required")
        admin_password = os.getenv("PINEGRAF_ADMIN_PASSWORD")
        if not admin_password:
            raise RuntimeError("PINEGRAF_ADMIN_PASSWORD is required")
        return Settings(
            database_url=database_url,
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            pdl_api_key=os.getenv("PDL_API_KEY", ""),
            pinegraf_admin_password=admin_password,
            admin_session_secret=admin_secret,
            admin_session_max_age_seconds=int(os.getenv("ADMIN_SESSION_MAX_AGE_SECONDS", "28800")),
            secure_cookies=os.getenv("SECURE_COOKIES", "true"),
            pinegraf_contact=os.getenv("PINEGRAF_CONTACT", "ops@example.com"),
            max_pages=int(os.getenv("MAX_PAGES", "10000")),
            crawl_concurrency=int(os.getenv("CRAWL_CONCURRENCY", "10")),
            crawl_liveness_check_interval=int(os.getenv("CRAWL_LIVENESS_CHECK_INTERVAL", "25")),
            recrawl_default_days=int(os.getenv("PINEGRAF_RECRAWL_DEFAULT_DAYS", "7")),
            snippet_max_chars=int(os.getenv("PINEGRAF_SNIPPET_MAX_CHARS", "400")),
            db_pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
            db_max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
            db_pool_recycle_seconds=int(os.getenv("DB_POOL_RECYCLE_SECONDS", "1800")),
            db_pool_pre_ping=os.getenv("DB_POOL_PRE_PING", "true"),
            use_mock_embeddings=os.getenv("USE_MOCK_EMBEDDINGS", "false"),
            demo_mode=os.getenv(
                "PINEGRAF_DEMO_MODE",
                "true" if os.getenv("PINEGRAF_ENV") == "demo" else "false",
            ),
            extraction_model=os.getenv("EXTRACTION_MODEL", "gpt-5.4-mini"),
            workspace_display_name=os.getenv("WORKSPACE_DISPLAY_NAME", "Tuck School of Business"),
            workspace_slug=os.getenv("WORKSPACE_SLUG", "tuck"),
            uploads_dir=os.getenv("UPLOADS_DIR", "/tmp/pinegraf_uploads"),
        )
    except ValidationError as exc:
        raise RuntimeError(f"Invalid configuration: {exc}") from exc
