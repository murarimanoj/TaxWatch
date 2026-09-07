from datetime import UTC, date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, HttpUrl


class Source(StrEnum):
    RBI = "rbi"
    CBDT = "cbdt"
    SEBI = "sebi"
    GST = "gst"
    MCA = "mca"


class BaseRegulatoryDocument(BaseModel):
    source: Source
    title: str
    published_date: date | None = None
    document_type: str = "notification"
    detail_url: HttpUrl
    attachment_url: HttpUrl | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class RegulatoryDocument(BaseRegulatoryDocument):
    content: str
    content_type: str
    document_hash: str
    content_hash: str
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RunSummary(BaseModel):
    source: Source
    discovered: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    failed: int = 0
    errors: list[str] = Field(default_factory=list)


class RegulatorySource(BaseModel):
    source_id: str
    authority: Source
    category: str
    adapter: str
    transport: str = "http"
    discovery_url: HttpUrl
    enabled: bool = True
