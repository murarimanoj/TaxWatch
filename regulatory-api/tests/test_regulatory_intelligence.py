from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage
from pymongo.errors import DuplicateKeyError

from regulatory_api.regulatory_intelligence.agents import RelationshipAgent
from regulatory_api.regulatory_intelligence.models import (
    ClientProfile,
    ImpactOutput,
    RelationshipOutput,
)
from regulatory_api.regulatory_intelligence.service import (
    IntelligenceService,
    digest,
    match_client,
    snapshot,
    validate_findings,
)


@pytest.fixture
def publication():
    return {
        "source": "cbdt",
        "document_hash": "new",
        "title": "New notification",
        "content_hash": "version",
        "content": "This notification amends notification 1/2025.",
        "detail_url": "https://example.gov.in/new",
    }


@pytest.fixture
def relations():
    return RelationshipOutput.model_validate(
        {
            "relationships": [
                {
                    "target_document_id": "cbdt:old",
                    "relationship_type": "AMENDS",
                    "scope": "partial",
                    "affected_provisions": ["Rule 1"],
                    "effective_from": None,
                    "explanation": "Amends Rule 1",
                    "evidence": [
                        {
                            "document_id": "cbdt:new",
                            "quote": "This notification amends notification 1/2025.",
                        }
                    ],
                }
            ],
            "unresolved_references": [],
            "uncertainties": [],
        }
    )


@pytest.fixture
def impact():
    return ImpactOutput.model_validate(
        {
            "summary": "Review changes",
            "what_changed": ["Rule amended"],
            "affected_provisions": ["Rule 1"],
            "ca_actions": ["Review applicability"],
            "effective_from": None,
            "deadlines": [],
            "urgency": "unknown",
            "applicability_rules": [{"field": "entity_type", "values": ["company"]}],
            "applicability_complete": True,
            "uncertainties": [],
            "evidence": [
                {
                    "document_id": "cbdt:new",
                    "quote": "This notification amends notification 1/2025.",
                }
            ],
        }
    )


def test_evidence_and_partial_scope(publication, relations, impact):
    docs = [snapshot(publication), snapshot({**publication, "document_hash": "old"})]
    validate_findings("cbdt:new", docs, relations, impact)
    relations.relationships[0].evidence[0].quote = "A completely fabricated assertion"
    with pytest.raises(ValueError, match="quote"):
        validate_findings("cbdt:new", docs, relations)


@pytest.mark.parametrize("target", ["sebi:old", "cbdt:new", "missing"])
def test_invalid_targets(publication, relations, target):
    relations.relationships[0].target_document_id = target
    with pytest.raises(ValueError, match="target"):
        validate_findings("cbdt:new", [snapshot(publication)], relations)


def test_partial_requires_provisions(publication, relations):
    relations.relationships[0].affected_provisions = []
    with pytest.raises(ValueError, match="provisions"):
        validate_findings(
            "cbdt:new",
            [snapshot(publication), snapshot({**publication, "document_hash": "old"})],
            relations,
        )


def test_matcher_is_conservative(impact):
    profile = ClientProfile(tenant_id="a", client_id="1", entity_type="Company")
    assert match_client(impact, profile)[0] == "matched"
    assert (
        match_client(impact, profile.model_copy(update={"entity_type": None}))[0]
        == "needs_review"
    )
    assert (
        match_client(impact, profile.model_copy(update={"entity_type": "individual"}))[
            0
        ]
        == "not_matched"
    )
    impact.applicability_complete = False
    assert match_client(impact, profile)[0] == "needs_review"
    impact.applicability_rules = []
    assert match_client(impact, profile)[0] == "needs_review"


def test_single_relationship_agent_call(publication, relations):
    client = MagicMock()
    client.respond.return_value = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "call",
                "name": "RelationshipOutput",
                "args": relations.model_dump(mode="json"),
            }
        ],
    )
    assert (
        RelationshipAgent(client).analyze(
            "cbdt:new",
            [snapshot(publication), snapshot({**publication, "document_hash": "old"})],
        )
        == relations
    )
    client.respond.assert_called_once()


def test_duplicate_run_skips_model(publication):
    db, model = MagicMock(), MagicMock()
    db.regulatory_documents.find_one.return_value = publication
    db.processing_runs.find_one_and_update.side_effect = DuplicateKeyError("duplicate")
    service = IntelligenceService(db, model)
    service.candidates = lambda _: []
    assert service.analyze("cbdt", "new")["status"] == "already_claimed_or_completed"
    model.respond.assert_not_called()


@pytest.mark.parametrize("failed_stage", [None, "relationships", "impact"])
def test_two_calls_success_and_version_change(
    publication, relations, impact, failed_stage
):
    db, model = MagicMock(), MagicMock()
    db.regulatory_documents.find_one.return_value = publication
    db.processing_runs.update_one.return_value.modified_count = 1
    responses = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "r",
                    "name": "RelationshipOutput",
                    "args": relations.model_dump(mode="json"),
                }
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "i",
                    "name": "ImpactOutput",
                    "args": impact.model_dump(mode="json"),
                }
            ],
        ),
    ]
    if failed_stage:
        index = 0 if failed_stage == "relationships" else 1
        bad = responses[index].model_copy(deep=True)
        args = bad.tool_calls[0]["args"]
        evidence = (
            args["relationships"][0]["evidence"] if index == 0 else args["evidence"]
        )
        evidence[0]["quote"] = "A fabricated quote that is absent from the source"
        responses.insert(index, bad)
    model.respond.side_effect = responses
    service = IntelligenceService(db, model)
    service.candidates = lambda _: [{**publication, "document_hash": "old"}]
    service.is_current = lambda _: True
    assert service.analyze("cbdt", "new")["status"] == "pending_review"
    assert model.respond.call_count == (3 if failed_stage else 2)
    if failed_stage:
        retry_payload = (
            model.respond.call_args_list[1 if failed_stage == "relationships" else 2]
            .args[0][1]
            .content
        )
        assert "validation_feedback" in retry_payload
    db.regulatory_impacts.replace_one.assert_called_once()
    assert (
        db.regulatory_impacts.replace_one.call_args.args[1]["review_status"]
        == "pending_review"
    )


def test_current_checks_text_even_if_hash_unchanged(publication):
    db = MagicMock()
    db.regulatory_documents.find_one.return_value = {
        **publication,
        "content": "changed",
    }
    assert not IntelligenceService(db).is_current(
        {"input_versions": [snapshot(publication)]}
    )


def test_approval_gates_alerts():
    db = MagicMock()
    db.regulatory_impacts.find_one.return_value = None
    with pytest.raises(ValueError, match="approved"):
        IntelligenceService(db).build_alerts("tenant-a", "impact")
    db.client_profiles.find.assert_not_called()


def test_profile_hash_is_order_independent():
    assert digest({"a": 1, "b": 2}) == digest({"b": 2, "a": 1})


def test_failed_agent_records_failure_without_publishing(publication):
    db, model = MagicMock(), MagicMock()
    db.regulatory_documents.find_one.return_value = publication
    model.respond.side_effect = ValueError("private invalid output")
    service = IntelligenceService(db, model)
    service.candidates = lambda _: []
    with pytest.raises(ValueError):
        service.analyze("cbdt", "new")
    changes = db.processing_runs.update_one.call_args.args[1]["$set"]
    assert changes["status"] == "failed"
    assert changes["error_type"] == "ValueError"
    assert changes["error_message"] == "private invalid output"
    assert "Traceback (most recent call last)" in changes["error_trace"]
    assert "ValueError: private invalid output" in changes["error_trace"]
    db.regulatory_impacts.replace_one.assert_not_called()


def test_private_alerts_require_token_and_use_server_tenant():
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from regulatory_api.app import Settings, create_app, get_repository

    app = create_app(
        Settings(
            _env_file=None,
            mongodb_uri=None,
            phase3_api_token="test-token",
            phase3_tenant_id="tenant-a",
        )
    )
    repository = MagicMock()
    app.dependency_overrides[get_repository] = lambda: repository
    with TestClient(app) as client, patch(
        "regulatory_api.app.IntelligenceService"
    ) as service:
        assert client.get("/api/client-alerts").status_code == 401
        assert (
            client.get(
                "/api/client-alerts", headers={"Authorization": "Bearer wrong"}
            ).status_code
            == 401
        )
        service.return_value.alerts.return_value = []
        response = client.get(
            "/api/client-alerts?tenant_id=tenant-b",
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code == 200
        service.return_value.alerts.assert_called_once_with("tenant-a")


def test_alert_build_is_tenant_scoped_and_deduplicated(impact):
    db = MagicMock()
    db.regulatory_impacts.find_one.return_value = {
        "review_status": "approved",
        "impact": impact.model_dump(mode="json"),
    }
    db.client_profiles.find.return_value = [
        ClientProfile(tenant_id="a", client_id="1", entity_type="company").model_dump()
    ]
    service = IntelligenceService(db)
    service.usable = lambda _: True
    assert service.build_alerts("a", "analysis") == 1
    first = db.client_alerts.replace_one.call_args.args[0]
    service.build_alerts("a", "analysis")
    assert db.client_alerts.replace_one.call_args.args[0] == first
    db.client_profiles.find.assert_called_with({"tenant_id": "a"}, {"_id": 0})


@pytest.mark.parametrize("separator", ["\n", "  ", "\u00a0", "\r\n"])
def test_quote_whitespace_restores_source(publication, relations, separator):
    publication["content"] = separator.join(publication["content"].split())
    docs = [snapshot(publication), snapshot({**publication, "document_hash": "old"})]
    validate_findings("cbdt:new", docs, relations)
    assert relations.relationships[0].evidence[0].quote == publication["content"]


@pytest.mark.parametrize(
    "quote",
    [
        "This notification ... notification 1/2025.",
        "This notification rescinds notification 1/2025.",
        "This notification amends Notification 1/2025.",
    ],
)
def test_quote_changes_remain_rejected(publication, relations, quote):
    relations.relationships[0].evidence[0].quote = quote
    docs = [snapshot(publication), snapshot({**publication, "document_hash": "old"})]
    with pytest.raises(ValueError, match="quote"):
        validate_findings("cbdt:new", docs, relations)


def test_invalid_quotes_exhaust_retry_without_publishing(publication, relations):
    db, model = MagicMock(), MagicMock()
    db.regulatory_documents.find_one.return_value = publication
    relations.relationships[0].evidence[
        0
    ].quote = "A fabricated quote absent from the supplied text"
    model.respond.return_value = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "bad",
                "name": "RelationshipOutput",
                "args": relations.model_dump(mode="json"),
            }
        ],
    )
    service = IntelligenceService(db, model)
    service.candidates = lambda _: [{**publication, "document_hash": "old"}]
    with pytest.raises(ValueError, match="quote"):
        service.analyze("cbdt", "new")
    assert model.respond.call_count == 2
    assert db.processing_runs.update_one.call_args.args[1]["$set"]["status"] == "failed"
    db.regulatory_impacts.replace_one.assert_not_called()
    db.document_relationships.replace_one.assert_not_called()


@pytest.mark.parametrize("specialist", ["relationships", "impact"])
def test_excerpt_ids_resolve_to_exact_source(
    publication, relations, impact, specialist
):
    import json

    from regulatory_api.regulatory_intelligence.agents import TaxIntelligenceAgent

    client = MagicMock()
    output = relations if specialist == "relationships" else impact
    args = output.model_dump(mode="json")
    evidence = (
        args["relationships"][0]["evidence"]
        if specialist == "relationships"
        else args["evidence"]
    )
    evidence[0]["quote"] = "source-excerpt-0000"
    client.respond.return_value = AIMessage(
        content="",
        tool_calls=[{"id": "citation", "name": type(output).__name__, "args": args}],
    )
    docs = [snapshot(publication), snapshot({**publication, "document_hash": "old"})]
    if specialist == "relationships":
        result = RelationshipAgent(client).analyze("cbdt:new", docs)
        validate_findings("cbdt:new", docs, result)
        quote = result.relationships[0].evidence[0].quote
    else:
        result = TaxIntelligenceAgent(client).analyze("cbdt:new", docs, relations)
        validate_findings("cbdt:new", docs, relations, result)
        quote = result.evidence[0].quote
    assert quote == publication["content"]
    payload = json.loads(client.respond.call_args.args[0][1].content)
    assert payload["documents"][0]["evidence_excerpts"][0]["text"] == quote
    assert "evidence_excerpts" not in docs[0]
    assert "text" not in payload["documents"][0]


def test_excerpt_catalog_is_bounded_and_exact(publication):
    from regulatory_api.regulatory_intelligence.agents import citation_payload

    publication["content"] = "An exact source sentence.\n" * 700
    doc = snapshot(publication)
    payload, excerpts = citation_payload({"documents": [doc]})
    catalog = payload["documents"][0]["evidence_excerpts"]
    assert "".join(item["text"] for item in catalog) == doc["text"]
    assert all(15 <= len(item["text"]) <= 1000 for item in catalog)
    assert all(
        excerpts[(doc["document_id"], item["id"])] == item["text"] for item in catalog
    )
    assert ("cbdt:other", catalog[0]["id"]) not in excerpts


@pytest.mark.parametrize("target", ["cbdt:new", "cbdt:missing", "sebi:old"])
def test_analysis_omits_invalid_targets_and_requires_review(
    publication, relations, impact, target
):
    import json

    db, model = MagicMock(), MagicMock()
    db.regulatory_documents.find_one.return_value = publication
    db.processing_runs.update_one.return_value.modified_count = 1
    relations.relationships[0].target_document_id = target
    model.respond.side_effect = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "r",
                    "name": "RelationshipOutput",
                    "args": relations.model_dump(mode="json"),
                }
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "i",
                    "name": "ImpactOutput",
                    "args": impact.model_dump(mode="json"),
                }
            ],
        ),
    ]
    service = IntelligenceService(db, model)
    service.candidates = lambda _: []
    service.is_current = lambda _: True
    assert service.analyze("cbdt", "new")["status"] == "pending_review"
    db.document_relationships.replace_one.assert_not_called()
    record = db.regulatory_impacts.replace_one.call_args.args[1]
    assert not record["impact"]["applicability_complete"]
    assert target in record["relationships"]["unresolved_references"][0]
    payload = json.loads(model.respond.call_args_list[0].args[0][1].content)
    assert payload["allowed_target_document_ids"] == []


def test_agent_preserves_valid_edges_among_invalid_targets(publication, relations):
    client = MagicMock()
    invalid = relations.relationships[0].model_copy(deep=True)
    invalid.target_document_id = "cbdt:new"
    relations.relationships.append(invalid)
    client.respond.return_value = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "r",
                "name": "RelationshipOutput",
                "args": relations.model_dump(mode="json"),
            }
        ],
    )
    docs = [snapshot(publication), snapshot({**publication, "document_hash": "old"})]
    result = RelationshipAgent(client).analyze("cbdt:new", docs)
    assert len(result.relationships) == 1
    assert result.relationships[0].target_document_id == "cbdt:old"
    assert result.unresolved_references
    validate_findings("cbdt:new", docs, result)
