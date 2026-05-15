from __future__ import annotations

from functools import lru_cache

from dotenv import load_dotenv
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import ValidationError
from pydantic import field_validator
import os


load_dotenv()


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    openai_api_key: str = Field(default="")
    serpapi_api_key: str = Field(default="")
    database_url: str = Field(default="sqlite:///./tuckscout.db")
    use_mock_search: bool = Field(default=True)
    use_mock_extract: bool = Field(default=True)
    use_mock_query: bool = Field(default=True)

    @field_validator("use_mock_search", "use_mock_extract", "use_mock_query", mode="before")
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
            database_url=os.getenv("DATABASE_URL", "sqlite:///./tuckscout.db"),
            use_mock_search=os.getenv("USE_MOCK_SEARCH", "true"),
            use_mock_extract=os.getenv("USE_MOCK_EXTRACT", "true"),
            use_mock_query=os.getenv("USE_MOCK_QUERY", "true"),
        )
    except ValidationError as exc:
        raise RuntimeError(f"Invalid configuration: {exc}") from exc
