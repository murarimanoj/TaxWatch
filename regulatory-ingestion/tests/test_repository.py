from datetime import date, datetime
from pathlib import Path

from bson import BSON

from regulatory_ingestion.config import load_source_catalog
from regulatory_ingestion.domain import (
    DocumentArtifact,
    DocumentRelationship,
    IngestionJob,
    ProcessingRun,
    RegulatoryDocument,
    RegulatoryProvision,
    RelationshipEndpoint,
    Source,
)
from regulatory_ingestion.repository import configured_sources, to_bson


def test_configured_sources_builds_one_record_per_page():
    catalog = load_source_catalog(Path("config/sources.toml"))
    records = configured_sources(catalog)

    assert len(records) == 7
    assert {record.source_id for record in records} == {
        "source_rbi_notification",
        "source_cbdt_notification",
        "source_cbdt_circular",
        "source_sebi_public_notices",
        "source_sebi_press_release",
        "source_gst_notification",
        "source_mca_circular",
    }
    assert {record.authority for record in records} == {
        Source.RBI,
        Source.CBDT,
        Source.SEBI,
        Source.GST,
        Source.MCA,
    }
    assert all(record.enabled for record in records)


def test_regulatory_document_payload_is_bson_encodable():
    document = RegulatoryDocument(
        source=Source.CBDT,
        title="Notification No. 117/2026",
        published_date=date(2026, 8, 25),
        document_type="notification",
        detail_url="https://www.incometaxindia.gov.in/documents/d/guest/example",
        content="Example",
        content_type="text/html",
        document_hash="document123",
        content_hash="abc123",
    )
    payload = to_bson(document.model_dump(mode="python"))

    BSON.encode(payload)
    assert isinstance(payload["detail_url"], str)
    assert isinstance(payload["published_date"], datetime)


def test_core_regulatory_models_are_bson_encodable():
    models = [
        DocumentArtifact(
            document_id="doc1",
            artifact_type="pdf",
            source_url="https://example.test/notice.pdf",
            mime_type="application/pdf",
            file_hash="abc",
        ),
        RegulatoryProvision(
            provision_id="section_194q",
            provision_type="section",
            law="Income-tax Act",
            number="194Q",
            title="TDS on goods",
        ),
        DocumentRelationship(
            source=RelationshipEndpoint(type="document", id="doc1"),
            relationship_type="CLARIFIES",
            target=RelationshipEndpoint(type="document", id="doc2"),
        ),
        IngestionJob(
            source_id="source_cbdt_notification",
            status="processing",
            current_stage="embedding",
        ),
        ProcessingRun(
            document_id="doc1",
            process_type="summary",
            model="test",
            input_hash="abc",
            status="completed",
        ),
    ]
    for model in models:
        BSON.encode(to_bson(model.model_dump(mode="python")))
