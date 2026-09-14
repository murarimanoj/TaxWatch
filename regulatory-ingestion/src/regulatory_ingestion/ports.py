from typing import Protocol

from .domain import (
    BaseRegulatoryDocument,
    DocumentChunk,
    RegulatoryDocument,
    RegulatorySource,
    RunSummary,
    Source,
)


class Embedder(Protocol):
    model: str
    dimensions: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class SourceAdapter(Protocol):
    source: Source

    def discover(self, limit: int) -> list[BaseRegulatoryDocument]: ...


class DocumentRepository(Protocol):
    def ensure_indexes(self) -> None: ...
    def sync_sources(self, sources: list[RegulatorySource]) -> None: ...
    def upsert(self, document: RegulatoryDocument) -> str: ...
    def iter_documents(
        self, source: Source | None, limit: int
    ) -> list[RegulatoryDocument]: ...
    def chunks_current(
        self, document: RegulatoryDocument, model: str, expected_count: int
    ) -> bool: ...
    def replace_chunks(
        self, document: RegulatoryDocument, chunks: list[DocumentChunk]
    ) -> None: ...
    def record_run(self, summary: RunSummary) -> None: ...
