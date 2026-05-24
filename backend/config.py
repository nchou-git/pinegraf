from __future__ import annotations

import os
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

    pinegraf_contact: str = Field(default="ops@example.com")
    max_pages: int = Field(default=1000, ge=1)
    use_mock_embeddings: bool = Field(default=False)

    @field_validator("use_mock_embeddings", mode="before")
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
        return Settings(
            database_url=os.getenv("DATABASE_URL", "sqlite:///./pinegraf.db"),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            pinegraf_admin_password=os.getenv("PINEGRAF_ADMIN_PASSWORD", "pinegraf"),
            site_auth_user=os.getenv("SITE_AUTH_USER", "pinegraf"),
            site_auth_password=os.getenv("SITE_AUTH_PASSWORD", ""),
            pinegraf_contact=os.getenv("PINEGRAF_CONTACT", "ops@example.com"),
            max_pages=int(os.getenv("MAX_PAGES", "1000")),
            use_mock_embeddings=os.getenv("USE_MOCK_EMBEDDINGS", "false"),
        )
    except ValidationError as exc:
        raise RuntimeError(f"Invalid configuration: {exc}") from exc
