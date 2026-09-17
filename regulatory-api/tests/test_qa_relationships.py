from datetime import date
from unittest.mock import MagicMock

import pytest

from regulatory_api.chat import Citation
from regulatory_api.models import Authority
from regulatory_api.qa.relationships import RelationshipLookup
from regulatory_api.regulatory_intelligence.service import snapshot

AS_OF = date(2026, 9, 16)


@pytest.fixture
def graph():
    old = {
        "source": "cbdt",
        "document_hash": "old",
        "title": "Old rule",
        "content": "File annually.",
        "content_hash": "old-version",
        "published_date": "2025-01-01",
        "detail_url": "https://example.gov/old",
    }
    new = {
        "source": "cbdt",
        "document_hash": "new",
        "title": "Amendment",
        "content": "This partially amends the annual filing rule from 1 October 2026.",
        "content_hash": "new-version",
        "published_date": "2026-09-01",
        "detail_url": "https://example.gov/new",
    }
    documents = {"old": old, "new": new}
    edge = {
        "_id": "edge",
        "analysis_id": "run",
        "review_status": "approved",
        "verification_status": "verified",
        "source": {"id": "cbdt:new"},
        "target": {"id": "cbdt:old"},
        "relationship_type": "AMENDS",
        "scope": "partial",
        "effective_from": "2026-10-01",
        "affected_provisions": ["Rule 1"],
        "input_versions": [snapshot(new), snapshot(old)],
        "evidence": [{"document_id": "cbdt:new", "quote": new["content"]}],
    }
    db = MagicMock()
    db.document_relationships.find.return_value.sort.return_value.limit.return_value = [
        edge
    ]
    db.regulatory_documents.find_one.side_effect = lambda q, projection: documents.get(
        q["document_hash"]
    )
    db.processing_runs.find_one.return_value = {"_id": "run"}
    seed = Citation(
        id="S1",
        source="cbdt",
        document_hash="old",
        title="Old rule",
        url=old["detail_url"],
        published_date="2025-01-01",
        passage=old["content"],
    )
    return RelationshipLookup(db), db, edge, documents, seed


def test_bidirectional_lookup_reads_original_quotes_and_preserves_future_scope(graph):
    lookup, db, _, _, seed = graph
    records, hints = lookup.expand([seed], Authority.CBDT, None, date(2026, 9, 16))
    assert {r["document_hash"] for r in records} == {"old", "new"}
    assert hints[0]["source_document_id"] == "cbdt:new"
    assert hints[0]["scope"] == "partial"
    assert hints[0]["temporal_context"] == "future"
    assert "partially amends" in records[0]["passage"]
    query = db.document_relationships.find.call_args.args[0]
    assert query["$or"] == [
        {"source.id": {"$in": ["cbdt:old"]}},
        {"target.id": {"$in": ["cbdt:old"]}},
    ]
    db.client_profiles.find.assert_not_called()
    db.regulatory_documents.update_one.assert_not_called()


@pytest.mark.parametrize("status", ["pending_review", "rejected"])
def test_unapproved_edges_are_not_used(graph, status):
    lookup, _, edge, _, seed = graph
    edge["review_status"] = status
    assert lookup.expand([seed], None, None, AS_OF) == ([], [])


def test_stale_evidence_rejected_even_when_content_hash_not_updated(graph):
    lookup, _, _, docs, seed = graph
    docs["new"]["content"] += " Changed later."
    assert lookup.expand([seed], None, None, AS_OF) == ([], [])


def test_unsuccessful_analysis_is_not_used(graph):
    lookup, db, _, _, seed = graph
    db.processing_runs.find_one.return_value = None
    assert lookup.expand([seed], None, None, AS_OF) == ([], [])


def test_cross_source_context_is_not_read(graph):
    lookup, db, edge, _, seed = graph
    edge["input_versions"][0]["document_id"] = "rbi:new"
    edge["source"]["id"] = "rbi:new"
    assert lookup.expand([seed], Authority.CBDT, None, AS_OF) == ([], [])
    assert not any(
        call.args[0]["source"] == "rbi"
        for call in db.regulatory_documents.find_one.call_args_list
    )


def test_publication_year_scope_also_applies_to_related_documents(graph):
    lookup, _, _, _, seed = graph
    assert lookup.expand([seed], Authority.CBDT, 2025, AS_OF) == ([], [])


def test_missing_effective_date_remains_unknown(graph):
    lookup, _, edge, _, seed = graph
    edge["effective_from"] = None
    _, hints = lookup.expand([seed], None, None, AS_OF)
    assert hints[0]["temporal_context"] == "unknown"


def test_fabricated_graph_quote_is_not_forwarded(graph):
    lookup, _, edge, _, seed = graph
    edge["evidence"][0]["quote"] = "An invented legal requirement"
    assert lookup.expand([seed], None, None, AS_OF) == ([], [])
