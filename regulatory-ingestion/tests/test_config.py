from pathlib import Path

import pytest

from regulatory_ingestion.config import load_source_catalog
from regulatory_ingestion.domain import Source
from regulatory_ingestion.registry import select_source_pages


def test_source_catalog_contains_all_configured_pages():
    catalog = load_source_catalog(Path("config/sources.toml"))
    assert len(catalog.sources["cbdt"].pages) == 2
    assert len(catalog.sources["sebi"].pages) == 2
    assert str(catalog.sources["rbi"].pages[0].url).startswith("https://www.rbi.org.in/")
    assert catalog.sources["sebi"].pages[0].document_type == "public_notices"
    assert str(catalog.sources["gst"].pages[0].url).endswith("cgst-tax-notification")
    assert catalog.sources["mca"].pages[0].transport == "playwright"
    assert str(catalog.sources["mca"].pages[0].url).endswith("circulars.html")


def test_select_source_pages_filters_by_document_type():
    catalog = load_source_catalog(Path("config/sources.toml"))

    pages = select_source_pages(Source.CBDT, catalog, "circular")

    assert len(pages) == 1
    assert pages[0].document_type == "circular"


def test_select_source_pages_returns_all_pages_without_document_type():
    catalog = load_source_catalog(Path("config/sources.toml"))

    pages = select_source_pages(Source.CBDT, catalog)

    assert {page.document_type for page in pages} == {"notification", "circular"}


def test_select_source_pages_rejects_unknown_document_type():
    catalog = load_source_catalog(Path("config/sources.toml"))

    with pytest.raises(ValueError, match="Available types: circular, notification"):
        select_source_pages(Source.CBDT, catalog, "master_direction")
