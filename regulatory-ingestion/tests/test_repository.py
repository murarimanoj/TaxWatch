from datetime import date, datetime
from pathlib import Path

from bson import BSON

from regulatory_ingestion.config import load_source_catalog
from regulatory_ingestion.domain import RegulatoryDocument, Source
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
