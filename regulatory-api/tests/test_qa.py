import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from pymongo.errors import PyMongoError

from regulatory_api.app import Settings, create_app, get_repository
from regulatory_api.chat import ChatRequest, ChatResponse
from regulatory_api.llm import ChatError
from regulatory_api.qa.orchestrator import ChatOrchestrator


def call(name, **args):
    return AIMessage(
        content="", tool_calls=[{"id": "call", "name": name, "args": args}]
    )


def route(name="tax", queries=None):
    return call("route_question", route=name, queries=queries or ["filing"], as_of=None)


def findings(
    stage="tax_intelligence", evidence_ids=None, text="File annually.", **kwargs
):
    return call(
        stage,
        findings=[
            {"kind": "conclusion", "text": text, "evidence_ids": evidence_ids or ["E1"]}
        ],
        missing_information=kwargs.get("missing", []),
        insufficient_evidence=kwargs.get("insufficient", False),
    )


def composed(ids=None):
    return call("compose_answer", finding_ids=ids or ["F1"])


def passage(source="cbdt", hash="doc", year="2026"):
    return {
        "source": source,
        "document_hash": hash,
        "title": "Filing circular",
        "url": "https://example.gov/notice",
        "published_date": f"{year}-01-01",
        "passage": "File annually.",
        "offset": 0,
    }


def setup(outputs, passages=None, supported=None):
    search, model, graph = MagicMock(), MagicMock(), MagicMock()
    search.search.return_value = passages if passages is not None else [passage()]
    # Script an independent reviewer response before the first composition call.
    scripted, count, reviewed = [], 0, False
    for output in outputs:
        invocation = output.tool_calls[0]
        if invocation["name"] == "compose_answer" and not reviewed:
            scripted.append(
                call(
                    "verify_evidence",
                    supported_finding_ids=(
                        supported
                        if supported is not None
                        else [f"F{i + 1}" for i in range(count)]
                    ),
                )
            )
            reviewed = True
        if invocation["name"] in (
            "document_analysis",
            "relationship_analysis",
            "tax_intelligence",
            "comparison_analysis",
        ):
            items = invocation["args"].get("findings", [])
            if all("E99" not in item["evidence_ids"] for item in items):
                count += len(items)
        scripted.append(output)
    model.respond.side_effect = scripted
    graph.expand.return_value = ([], [])
    service = ChatOrchestrator(search, model, relationships=graph, diagnostics=True)
    return service, search, model, graph


def stages(model):
    return [
        call.args[1][0]["function"]["name"] for call in model.respond.call_args_list
    ]


@pytest.mark.parametrize(
    "mode,stage", [("summary", "document_analysis"), ("tax", "tax_intelligence")]
)
def test_simple_routes_skip_relationship_agent(mode, stage):
    service, _, model, graph = setup([route(mode), findings(stage), composed()])
    result = service.answer(ChatRequest(question="Explain this filing", source="cbdt"))
    assert stages(model) == [
        "route_question",
        stage,
        "verify_evidence",
        "compose_answer",
    ]
    assert "File annually. [S1]" in result.answer
    assert result.citations[0].passage == "File annually."
    assert result.insufficient_evidence is False
    graph.expand.assert_not_called()


@pytest.mark.parametrize(
    "mode,stage",
    [("current_position", "tax_intelligence"), ("comparison", "comparison_analysis")],
)
def test_relationship_precedes_interpretation(mode, stage):
    service, _, model, graph = setup(
        [
            route(mode),
            findings("relationship_analysis"),
            findings(stage),
            composed(["F2", "F1"]),
        ]
    )
    result = service.answer(
        ChatRequest(question="Explain these rules", as_of="2026-09-01")
    )
    assert stages(model) == [
        "route_question",
        "relationship_analysis",
        stage,
        "verify_evidence",
        "compose_answer",
    ]
    context = json.loads(model.respond.call_args_list[2].args[0][1].content)
    assert context["relationship_findings"]["findings"]
    assert context["as_of"] == "2026-09-01"
    assert "not a certification" in result.answer
    graph.expand.assert_called_once()


def test_current_question_cannot_skip_relationship_stage():
    service, _, model, _ = setup(
        [route("summary"), findings("relationship_analysis"), findings(), composed()]
    )
    service.answer(ChatRequest(question="Is this still valid?"))
    assert "relationship_analysis" in stages(model)


def test_relationship_only_skips_tax():
    service, _, model, _ = setup(
        [route("relationship"), findings("relationship_analysis"), composed()]
    )
    service.answer(ChatRequest(question="Which circular does this amend?"))
    assert stages(model) == [
        "route_question",
        "relationship_analysis",
        "verify_evidence",
        "compose_answer",
    ]


def test_no_evidence_stops_before_specialists():
    service, _, model, _ = setup([route()], passages=[])
    result = service.answer(ChatRequest(question="Missing regulation"))
    assert result.insufficient_evidence
    assert result.citations == []
    assert model.respond.call_count == 1


def test_source_and_year_scope_is_enforced_before_model_input():
    service, search, model, _ = setup(
        [route()], passages=[passage("rbi"), passage(year="2025")]
    )
    result = service.answer(ChatRequest(question="Filing?", source="cbdt", year=2026))
    assert result.insufficient_evidence
    assert model.respond.call_count == 1
    args, scope = search.search.call_args.args
    assert args.year == 2026 and args.source.value == "cbdt" and scope.value == "cbdt"


def test_exact_references_are_preserved_for_both_comparison_sides():
    service, search, _, _ = setup(
        [
            route("comparison"),
            findings("relationship_analysis"),
            findings("comparison_analysis"),
            composed(),
        ]
    )
    service.answer(ChatRequest(question="Compare 116/2026 and 115/2025"))
    queries = [call.args[0].query for call in search.search.call_args_list]
    assert queries[:2] == ["116/2026", "115/2025"]
    assert len(queries) <= 3


def test_unknown_evidence_retries_and_never_reaches_composer():
    service, _, model, _ = setup(
        [route(), findings(evidence_ids=["E99"]), findings(evidence_ids=["E99"])]
    )
    result = service.answer(ChatRequest(question="Filing?"))
    assert result.insufficient_evidence
    assert "did not produce validated findings" in result.answer
    assert model.respond.call_count == 3
    assert "compose_answer" not in stages(model)


def test_repaired_findings_succeed():
    service, _, model, _ = setup(
        [route(), findings(evidence_ids=["E99"]), findings(), composed()]
    )
    result = service.answer(ChatRequest(question="Filing?"))
    assert result.insufficient_evidence is False
    assert model.respond.call_count == 5
    assert "failed validation" in model.respond.call_args_list[2].args[0][0].content


def test_composer_cannot_invent_findings_or_citations():
    service, _, model, _ = setup(
        [route(), findings(), composed(["F99"]), composed(["F99"])]
    )
    result = service.answer(ChatRequest(question="Filing?"))
    assert "F99" not in result.answer
    assert "File annually. [S1]" in result.answer
    assert len(result.citations) == 1
    assert model.respond.call_count == 5


def test_call_budget_is_shared_by_all_stages():
    service, _, model, _ = setup(
        [
            route("current_position"),
            findings("relationship_analysis", evidence_ids=["E99"]),
            findings("relationship_analysis"),
            findings(evidence_ids=["E99"]),
            findings(),
            composed(["F99"]),
        ]
    )
    result = service.answer(ChatRequest(question="Latest position?"))
    assert model.respond.call_count == 6
    assert result.citations


def test_graph_unavailability_is_disclosed():
    service, _, _, graph = setup(
        [route("relationship"), findings("relationship_analysis"), composed()]
    )
    graph.expand.side_effect = PyMongoError("secret backend")
    result = service.answer(ChatRequest(question="Amendments?"))
    assert result.insufficient_evidence
    assert "lookup was unavailable" in result.answer
    assert "secret backend" not in result.answer


def test_history_only_router_and_client_collections_never_read():
    service, search, model, _ = setup([route(), findings(), composed()])
    service.answer(
        ChatRequest(
            question="What about my firm?",
            history=[{"role": "assistant", "content": "prior unsupported statement"}],
        )
    )
    assert (
        "prior unsupported statement"
        in model.respond.call_args_list[0].args[0][1].content
    )
    assert (
        "prior unsupported statement"
        not in model.respond.call_args_list[1].args[0][1].content
    )
    search.database.client_profiles.find.assert_not_called()


def test_diagnostics_report_stages_without_question_or_passage(caplog):
    service, _, _, _ = setup([route(), findings(), composed()])
    service.answer(ChatRequest(question="Sensitive question text"))
    logs = "\n".join(r.message for r in caplog.records)
    assert "stage=tax_intelligence event=accepted" in logs
    assert "stage=orchestrator event=completed" in logs
    assert "Sensitive question text" not in logs
    assert "File annually" not in logs


def test_router_failures_raise_sanitized_error():
    service, _, _, _ = setup([call("unknown"), call("unknown")])
    with pytest.raises(ChatError, match="routing failed"):
        service.answer(ChatRequest(question="Question"))


def test_grounding_reviewer_can_reject_all_syntactically_valid_findings():
    service, _, model, _ = setup([route(), findings(), composed()], supported=[])
    result = service.answer(ChatRequest(question="Filing?"))
    assert result.insufficient_evidence
    assert "did not substantiate" in result.answer
    assert "compose_answer" not in stages(model)


def test_grounding_reviewer_removes_unsupported_findings_before_composition():
    service, _, model, _ = setup(
        [
            route("current_position"),
            findings("relationship_analysis"),
            findings(),
            composed(["F2"]),
        ],
        supported=["F2"],
    )
    result = service.answer(ChatRequest(question="Latest position?"))
    payload = json.loads(model.respond.call_args_list[-1].args[0][1].content)
    assert set(payload["findings"]) == {"F2"}
    assert result.insufficient_evidence
    assert "Some proposed conclusions were excluded" in result.answer


def test_graph_passages_become_server_owned_citations():
    service, _, _, graph = setup(
        [
            route("relationship"),
            findings("relationship_analysis", evidence_ids=["E2"]),
            composed(),
        ]
    )
    graph.expand.return_value = ([passage(hash="new")], [])
    result = service.answer(ChatRequest(question="What amended it?"))
    assert result.citations[0].document_hash == "new"
    assert result.citations[0].id == "S2"


@pytest.mark.parametrize(
    "enabled,implementation", [(True, "ChatOrchestrator"), (False, "ChatService")]
)
def test_endpoint_uses_selected_implementation(enabled, implementation):
    app = create_app(
        Settings(
            _env_file=None,
            mongodb_uri=None,
            openai_api_key="test",
            chat_multi_agent_enabled=enabled,
        )
    )
    app.dependency_overrides[get_repository] = lambda: MagicMock()
    answer = ChatResponse(
        answer="No evidence", citations=[], insufficient_evidence=True
    )
    with patch(
        f"regulatory_api.app.{implementation}.answer", return_value=answer
    ) as run, TestClient(app) as client:
        response = client.post(
            "/api/chat", json={"question": "Q", "as_of": "2026-09-01"}
        )
    assert response.status_code == 200
    assert run.call_args.args[0].as_of == date(2026, 9, 1)
    assert set(response.json()) == {
        "answer",
        "citations",
        "insufficient_evidence",
        "source",
        "year",
    }
