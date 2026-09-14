from datetime import date

from regulatory_ingestion.domain import RegulatoryDocument, Source
from regulatory_ingestion.processing import DocumentChunkProcessor, backfill_chunks


def make_document() -> RegulatoryDocument:
    return RegulatoryDocument(
        source=Source.CBDT,
        title="Test notification",
        published_date=date(2026, 1, 1),
        detail_url="https://example.com/notification",
        content="A test notification with enough content to chunk.",
        content_type="text/html",
        document_hash="document-1",
        content_hash="content-1",
    )


class FakeEmbedder:
    model = "test-model"
    dimensions = 2

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [[1.0, 0.0] for _ in texts]


class FakeRepository:
    def __init__(self, documents: list[RegulatoryDocument]) -> None:
        self.documents = documents
        self.chunks = []
        self.current = False
        self.received_source = None

    def iter_documents(
        self, source: Source | None, limit: int
    ) -> list[RegulatoryDocument]:
        self.received_source = source
        return [
            doc for doc in self.documents if source is None or doc.source == source
        ][:limit]

    def chunks_current(
        self, document: RegulatoryDocument, model: str, count: int
    ) -> bool:
        return self.current

    def replace_chunks(self, document: RegulatoryDocument, chunks: list) -> None:
        self.chunks = chunks


def test_backfill_embeds_stored_document() -> None:
    repository = FakeRepository([make_document()])
    embedder = FakeEmbedder()
    processor = DocumentChunkProcessor(repository, embedder, 30, 5)

    summary = backfill_chunks(repository, processor, Source.CBDT, 10)

    assert summary.scanned == 1
    assert summary.processed == 1
    assert summary.chunks_written == len(repository.chunks)
    assert embedder.calls == 1
    assert all(chunk.document_id == "document-1" for chunk in repository.chunks)
    assert repository.received_source == Source.CBDT


def test_backfill_skips_current_chunks_unless_forced() -> None:
    repository = FakeRepository([make_document()])
    repository.current = True
    embedder = FakeEmbedder()
    processor = DocumentChunkProcessor(repository, embedder, 100, 5)

    summary = backfill_chunks(repository, processor, None, 10)
    assert summary.skipped == 1
    assert embedder.calls == 0

    forced = backfill_chunks(repository, processor, None, 10, force=True)
    assert forced.processed == 1
    assert embedder.calls == 1
