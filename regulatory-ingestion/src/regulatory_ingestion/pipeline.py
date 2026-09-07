import logging

from .browser import BrowserClient
from .domain import RegulatoryDocument, RunSummary
from .extraction import ContentExtractor
from .hashing import content_hash, document_hash
from .http import HttpClient
from .ports import DocumentRepository, SourceAdapter

logger = logging.getLogger(__name__)


class IngestionPipeline:
    def __init__(
        self,
        adapter: SourceAdapter,
        client: HttpClient,
        extractor: ContentExtractor,
        repository: DocumentRepository,
        browser: BrowserClient | None = None,
    ) -> None:
        self.adapter = adapter
        self.client = client
        self.extractor = extractor
        self.repository = repository
        self.browser = browser

    def run(self, limit: int) -> RunSummary:
        summary = RunSummary(source=self.adapter.source)
        try:
            candidates = self.adapter.discover(limit)
            summary.discovered = len(candidates)
            for candidate in candidates:
                try:
                    target = str(candidate.attachment_url or candidate.detail_url)
                    if candidate.metadata.get("transport") == "playwright":
                        if self.browser is None:
                            raise RuntimeError("Playwright transport is configured but unavailable")
                        response = self.browser.get(target)
                    else:
                        response = self.client.get(target)
                    content_type = response.headers.get("content-type", "application/octet-stream")
                    text = self.extractor.extract(response.content, content_type, target)
                    document = RegulatoryDocument(
                        **candidate.model_dump(),
                        content=text,
                        content_type=content_type,
                        document_hash=document_hash(
                            candidate.source,
                            candidate.published_date,
                            candidate.title,
                        ),
                        content_hash=content_hash(response.content),
                    )
                    outcome = self.repository.upsert(document)
                    setattr(summary, outcome, getattr(summary, outcome) + 1)
                except Exception as exc:  # one bad publication must not abort the batch
                    logger.exception("Failed to ingest %s", candidate.detail_url)
                    summary.failed += 1
                    summary.errors.append(
                        f"{candidate.title}: {type(exc).__name__}: {exc}"
                    )
        except Exception as exc:
            logger.exception("Discovery failed for %s", self.adapter.source)
            summary.failed += 1
            summary.errors.append(f"discovery: {type(exc).__name__}: {exc}")
        self.repository.record_run(summary)
        return summary
