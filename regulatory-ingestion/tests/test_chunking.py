import pytest

from regulatory_ingestion.chunking import chunk_text


def test_chunk_text_covers_content_with_overlap():
    text = " ".join(f"word{i}" for i in range(80))
    chunks = chunk_text(text, size=100, overlap=20)
    assert len(chunks) > 1
    assert all(0 < len(chunk) <= 100 for chunk in chunks)
    assert chunks[0].startswith("word0")
    assert chunks[-1].endswith("word79")


def test_chunk_text_rejects_bad_overlap():
    with pytest.raises(ValueError):
        chunk_text("abc", size=10, overlap=10)


def test_chunk_text_empty_document():
    assert chunk_text(" \n ", size=20, overlap=5) == []
