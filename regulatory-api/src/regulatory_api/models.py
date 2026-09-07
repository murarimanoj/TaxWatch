from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Authority(StrEnum):
    RBI = "rbi"
    CBDT = "cbdt"
    SEBI = "sebi"
    GST = "gst"


class Document(BaseModel):
    source: Authority
    document_hash: str
    title: str
    published_date: datetime | None = None
    document_type: str
    detail_url: str
    attachment_url: str | None = None
    excerpt: str = ""


class DocumentDetail(Document):
    content: str = ""
    fetched_at: datetime | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class DocumentPage(BaseModel):
    items: list[Document]
    total: int
    page: int
    page_size: int


class AuthorityOverview(BaseModel):
    source: Authority
    total: int
    latest_year: int | None
    last_checked_at: datetime | None = None
    last_run_status: str | None = None
    preview: list[Document]


class Overview(BaseModel):
    total: int
    authorities: list[AuthorityOverview]
