from datetime import date
from typing import ClassVar

from regulatory_ingestion.domain import BaseRegulatoryDocument, Source
from regulatory_ingestion.pipeline import IngestionPipeline


class Adapter:
    source = Source.RBI

    def discover(self, limit):
        return [
            BaseRegulatoryDocument(
                source=self.source,
                title="Test notice",
                published_date=date(2026, 8, 20),
                detail_url="https://example.test/notice",
            )
        ]

class Response:
    content = b"<html><main>Regulatory content</main></html>"
    headers: ClassVar[dict[str, str]] = {"content-type": "text/html"}


class Client:
    def get(self, url):
        return Response()


class Extractor:
    def extract(self, body, content_type, url):
        return "Regulatory content"


class Repository:
    def __init__(self):
        self.documents = []
        self.runs = []

    def upsert(self, document):
        self.documents.append(document)
        return "inserted"

    def record_run(self, summary):
        self.runs.append(summary)


def test_pipeline_ingests_and_records_run():
    repository = Repository()
    summary = IngestionPipeline(Adapter(), Client(), Extractor(), repository).run(10)
    assert summary.discovered == 1
    assert summary.inserted == 1
    assert summary.failed == 0
    assert repository.documents[0].content == "Regulatory content"
    assert repository.documents[0].document_hash == (
        "f5739d0093e5669b59558cae7872cdeb28998078e3cad412f049a23ec48753be"
    )
    assert repository.documents[0].content_hash == (
        "295ba84869cdc2c1c42ffeea1811895a1e4e20d7d29d4278b09dfe6f3eacd7d6"
    )
    assert repository.runs == [summary]
