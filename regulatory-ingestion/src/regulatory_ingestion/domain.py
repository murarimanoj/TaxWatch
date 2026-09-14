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
    chunks_written: int = 0
    errors: list[str] = Field(default_factory=list)


class DocumentChunk(BaseModel):
    chunk_id: str
    document_id: str
    document_hash: str
    source: Source
    document_type: str
    title: str
    published_date: date | None
    detail_url: HttpUrl
    chunk_index: int
    text: str
    embedding: list[float]
    embedding_model: str
    content_hash: str
    artifact_id: str | None = None
    section: str | None = None
    page_start: int | None = None
    page_end: int | None = None


class DocumentArtifact(BaseModel):
    document_id: str
    artifact_type: str
    source_url: HttpUrl
    mime_type: str
    file_hash: str
    version: int = Field(default=1, ge=1)
    storage_key: str | None = None
    downloaded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RegulatoryProvision(BaseModel):
    provision_id: str
    provision_type: str
    law: str
    number: str
    title: str
    parent_provision_id: str | None = None
    status: str = "active"


class RelationshipEndpoint(BaseModel):
    type: str
    id: str


class RelationshipEvidence(BaseModel):
    document_id: str
    chunk_id: str | None = None
    page: int | None = None


class DocumentRelationship(BaseModel):
    source: RelationshipEndpoint
    relationship_type: str
    target: RelationshipEndpoint
    evidence: list[RelationshipEvidence] = Field(default_factory=list)
    verification_status: str = "unverified"
    confidence: float | None = Field(default=None, ge=0, le=1)
    effective_from: date | None = None


class IngestionJob(BaseModel):
    source_id: str
    document_id: str | None = None
    status: str
    current_stage: str
    attempt_count: int = Field(default=1, ge=1)
    error: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None


class ProcessingRun(BaseModel):
    document_id: str
    process_type: str
    model: str
    input_hash: str
    status: str
    prompt_version: str | None = None
    output: dict[str, object] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None


class RegulatorySource(BaseModel):
    source_id: str
    authority: Source
    category: str
    adapter: str
    transport: str = "http"
    discovery_url: HttpUrl
    enabled: bool = True
