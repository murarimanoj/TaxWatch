from datetime import date
from pathlib import Path

import pytest

from regulatory_ingestion.adapters import (
    CbdtAdapter,
    GstAdapter,
    McaAdapter,
    RbiAdapter,
    SebiAdapter,
)
from regulatory_ingestion.config import SourcePageConfig
from regulatory_ingestion.domain import Source

FIXTURES = Path(__file__).parent / "fixtures"


class Response:
    def __init__(self, text: str) -> None:
        self.text = text


class Client:
    def __init__(self, text: str) -> None:
        self.text = text

    def get(self, url: str) -> Response:
        return Response(self.text)


@pytest.mark.parametrize(
    ("adapter_type", "fixture", "source", "expected_title"),
    [
        (RbiAdapter, "rbi.html", Source.RBI, "Digital Lending Directions"),
        (CbdtAdapter, "cbdt.html", Source.CBDT, "Notification No. 117/2026"),
        (SebiAdapter, "sebi.html", Source.SEBI, "KYC framework update"),
        (
            SebiAdapter,
            "sebi_press_releases.html",
            Source.SEBI,
            "SEBI signs MoU with European Securities and Markets Authority",
        ),
        (
            McaAdapter,
            "mca.html",
            Source.MCA,
            "General Circular No. 05/2024 - Extension of time for filing PAS-7",
        ),
    ],
)
def test_listing_parsers(adapter_type, fixture, source, expected_title):
    adapter = adapter_type(client=None, pages=[])
    results = adapter.parse_listing((FIXTURES / fixture).read_text(), limit=10)
    assert len(results) == 1
    assert results[0].source == source
    assert results[0].title == expected_title
    assert results[0].published_date is not None


def test_candidate_preserves_configured_transport():
    adapter = CbdtAdapter(client=None, pages=[])
    page = SourcePageConfig(
        url="https://example.test/cbdt", document_type="notification", transport="playwright"
    )
    results = adapter.parse_listing((FIXTURES / "cbdt.html").read_text(), limit=1, page=page)
    assert results[0].metadata["transport"] == "playwright"


def test_rbi_listing_uses_date_from_group_header():
    adapter = RbiAdapter(client=None, pages=[])

    results = adapter.parse_listing((FIXTURES / "rbi.html").read_text(), limit=1)

    assert results[0].published_date == date(2026, 8, 20)


def test_discover_applies_limit_to_each_configured_page():
    html = (FIXTURES / "rbi.html").read_text()
    pages = [
        SourcePageConfig(
            url="https://example.test/rbi/notifications",
            document_type="notification",
        ),
        SourcePageConfig(
            url="https://example.test/rbi/master-directions",
            document_type="master_direction",
        ),
    ]
    adapter = RbiAdapter(client=Client(html), pages=pages)

    results = adapter.discover(limit=1)

    assert len(results) == 2
    assert {result.document_type for result in results} == {
        "notification",
        "master_direction",
    }


def test_gst_listing_selects_only_english_document():
    adapter = GstAdapter(client=None, pages=[])

    results = adapter.parse_listing((FIXTURES / "gst.html").read_text(), limit=1)

    assert len(results) == 1
    assert results[0].source == Source.GST
    assert results[0].published_date == date(2025, 11, 1)
    assert results[0].title == (
        "11/2025-Central Tax - "
        "Seeks to notify the CGST (Second Amendment) Rules 2025"
    )
    assert str(results[0].attachment_url).endswith("gst-ct-11-2025.pdf")
    assert "gst-ct-11h-2025.pdf" not in str(results[0].attachment_url)


def test_gst_full_column_date_takes_precedence_over_month_year_default():
    adapter = GstAdapter(client=None, pages=[])
    html = (FIXTURES / "gst.html").read_text().replace(
        "11/2025-Central Tax",
        "11/2025-Central Tax dated 27.03.2025",
        1,
    )

    results = adapter.parse_listing(html, limit=1)

    assert results[0].published_date == date(2025, 3, 27)
