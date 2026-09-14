import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class SourcePageConfig(BaseModel):
    url: HttpUrl
    document_type: str
    transport: Literal["http", "playwright"] = "http"
    timeout_seconds: float | None = Field(default=None, gt=0)


class SourceConfig(BaseModel):
    adapter: str
    pages: list[SourcePageConfig]


class SourceCatalog(BaseModel):
    sources: dict[str, SourceConfig]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mongodb_uri: str | None = None
    mongodb_database: str = "taxwatch"
    http_timeout_seconds: float = Field(default=30, gt=0)
    http_user_agent: str = "TaxWatch-Regulatory-Ingestion/0.1"
    max_items_per_run: int = Field(default=25, gt=0, le=500)
    source_config_path: Path = Path("config/sources.toml")
    playwright_headless: bool = False
    playwright_channel: str = "chrome"
    playwright_popup_timeout_seconds: float = Field(default=90, gt=0)
    playwright_user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
    )
    embeddings_enabled: bool = False
    openai_api_key: str | None = None
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = Field(default=1536, gt=0)
    chunk_size_chars: int = Field(default=2400, gt=0)
    chunk_overlap_chars: int = Field(default=300, ge=0)


def load_source_catalog(path: Path) -> SourceCatalog:
    if not path.is_absolute():
        path = Path.cwd() / path
    with path.open("rb") as config_file:
        return SourceCatalog.model_validate(tomllib.load(config_file))


@lru_cache
def get_settings() -> Settings:
    return Settings()
