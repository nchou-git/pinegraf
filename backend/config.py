from __future__ import annotations

import os
import secrets as secrets_mod
from functools import lru_cache

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

load_dotenv()


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    database_url: str = Field(default="sqlite:///./pinegraf.db")
    openai_api_key: str = Field(default="")

    pinegraf_admin_password: str = Field(default="pinegraf")
    site_auth_user: str = Field(default="pinegraf")
    site_auth_password: str = Field(default="")

    admin_session_secret: str = Field(default="")
    admin_session_max_age_seconds: int = Field(default=28800, ge=60)
    secure_cookies: bool = Field(default=True)

    pinegraf_contact: str = Field(default="ops@example.com")
    max_pages: int = Field(default=1000, ge=1)
    use_mock_embeddings: bool = Field(default=False)
    cheap_model: str = Field(default="gpt-4o-mini")
    frontier_model: str = Field(default="gpt-4o")

    workspace_display_name: str = Field(default="Tuck School of Business")
    workspace_slug: str = Field(default="tuck")
    workspace_tagline: str = Field(default="Where alumni stories connect.")

    uploads_dir: str = Field(default="/tmp/pinegraf_uploads")

    @field_validator("use_mock_embeddings", "secure_cookies", mode="before")
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
        admin_secret = os.getenv("ADMIN_SESSION_SECRET", "")
        if not admin_secret:
            admin_secret = secrets_mod.token_urlsafe(48)
        return Settings(
            database_url=os.getenv("DATABASE_URL", "sqlite:///./pinegraf.db"),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            pinegraf_admin_password=os.getenv("PINEGRAF_ADMIN_PASSWORD", "pinegraf"),
            site_auth_user=os.getenv("SITE_AUTH_USER", "pinegraf"),
            site_auth_password=os.getenv("SITE_AUTH_PASSWORD", ""),
            admin_session_secret=admin_secret,
            admin_session_max_age_seconds=int(os.getenv("ADMIN_SESSION_MAX_AGE_SECONDS", "28800")),
            secure_cookies=os.getenv("SECURE_COOKIES", "true"),
            pinegraf_contact=os.getenv("PINEGRAF_CONTACT", "ops@example.com"),
            max_pages=int(os.getenv("MAX_PAGES", "1000")),
            use_mock_embeddings=os.getenv("USE_MOCK_EMBEDDINGS", "false"),
            cheap_model=os.getenv("CHEAP_MODEL", "gpt-4o-mini"),
            frontier_model=os.getenv("FRONTIER_MODEL", "gpt-4o"),
            workspace_display_name=os.getenv("WORKSPACE_DISPLAY_NAME", "Tuck School of Business"),
            workspace_slug=os.getenv("WORKSPACE_SLUG", "tuck"),
            workspace_tagline=os.getenv("WORKSPACE_TAGLINE", "Where alumni stories connect."),
            uploads_dir=os.getenv("UPLOADS_DIR", "/tmp/pinegraf_uploads"),
        )
    except ValidationError as exc:
        raise RuntimeError(f"Invalid configuration: {exc}") from exc
