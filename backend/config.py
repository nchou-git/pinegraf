from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

load_dotenv()


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    openai_api_key: str = Field(default="")
    serpapi_api_key: str = Field(default="")
    database_url: str = Field(default="sqlite:///./pinegraf.db")
    use_mock_search: bool = Field(default=True)
    use_mock_extract: bool = Field(default=True)
    use_mock_query: bool = Field(default=True)
    use_mock_fetch: bool = Field(default=True)
    research_max_depth: int = Field(default=1, ge=0)
    research_pages_per_alum: int = Field(default=3, ge=1)

    @field_validator(
        "use_mock_search",
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
            serpapi_api_key=os.getenv("SERPAPI_API_KEY", ""),
            database_url=os.getenv("DATABASE_URL", "sqlite:///./pinegraf.db"),
            use_mock_search=os.getenv("USE_MOCK_SEARCH", "true"),
            use_mock_extract=os.getenv("USE_MOCK_EXTRACT", "true"),
            use_mock_query=os.getenv("USE_MOCK_QUERY", "true"),
            use_mock_fetch=os.getenv("USE_MOCK_FETCH", "true"),
            research_max_depth=int(os.getenv("RESEARCH_MAX_DEPTH", "1")),
            research_pages_per_alum=int(os.getenv("RESEARCH_PAGES_PER_ALUM", "3")),
        )
    except ValidationError as exc:
        raise RuntimeError(f"Invalid configuration: {exc}") from exc
