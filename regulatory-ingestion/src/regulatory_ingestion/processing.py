import logging

from pydantic import BaseModel, Field

from .chunking import chunk_text
from .domain import DocumentChunk, RegulatoryDocument, Source
from .ports import DocumentRepository, Embedder

logger = logging.getLogger(__name__)


class DocumentChunkProcessor:
    def __init__(
        self,
        repository: DocumentRepository,
        embedder: Embedder,
        chunk_size: int,
        chunk_overlap: int,
    ) -> None:
        self.repository = repository
        self.embedder = embedder
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def process(self, document: RegulatoryDocument, force: bool = False) -> int:
        texts = chunk_text(document.content, self.chunk_size, self.chunk_overlap)
        if not texts:
            raise ValueError("No extractable text for embeddings")
        if not force and self.repository.chunks_current(
            document, self.embedder.model, len(texts)
        ):
            return 0
        vectors = self.embedder.embed(texts)
        chunks = [
            DocumentChunk(
                chunk_id=f"{document.source.value}:{document.document_hash}:{index}",
                document_id=document.document_hash,
                document_hash=document.document_hash,
                source=document.source,
                document_type=document.document_type,
                title=document.title,
                published_date=document.published_date,
                detail_url=document.detail_url,
                chunk_index=index,
                text=chunk,
                embedding=vector,
                embedding_model=self.embedder.model,
                content_hash=document.content_hash,
            )
            for index, (chunk, vector) in enumerate(zip(texts, vectors, strict=True))
        ]
        self.repository.replace_chunks(document, chunks)
        return len(chunks)


class BackfillSummary(BaseModel):
    source: Source | None = None
    scanned: int = 0
    processed: int = 0
    skipped: int = 0
    chunks_written: int = 0
    failed: int = 0
    errors: list[str] = Field(default_factory=list)


def backfill_chunks(
    repository: DocumentRepository,
    processor: DocumentChunkProcessor,
    source: Source | None,
    limit: int,
    force: bool = False,
) -> BackfillSummary:
    summary = BackfillSummary(source=source)
    for document in repository.iter_documents(source, limit):
        summary.scanned += 1
        try:
            written = processor.process(document, force=force)
            if written:
                summary.processed += 1
                summary.chunks_written += written
            else:
                summary.skipped += 1
        except Exception as exc:
            logger.exception(
                "Failed to chunk stored document %s", document.document_hash
            )
            summary.failed += 1
            summary.errors.append(
                f"{document.document_hash}: {type(exc).__name__}: {exc}"
            )
    return summary
