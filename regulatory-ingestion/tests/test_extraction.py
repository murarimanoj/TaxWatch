from regulatory_ingestion.extraction import ContentExtractor


def test_html_body_is_not_treated_as_pdf_when_header_is_wrong():
    text = ContentExtractor().extract(
        b"<html><main>Document viewer</main></html>",
        "application/pdf",
        "https://example.test/document-pdf",
    )
    assert text == "Document viewer"
