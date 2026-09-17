import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from regulatory_api.app import Settings, create_app, get_repository
from regulatory_api.chat import (
    ChatError,
    ChatRequest,
    ChatService,
    PublicationSearch,
    SearchArgs,
)
from regulatory_api.models import Authority


def call(name: str, **arguments: object) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": name,
                "id": "test-call",
                "args": arguments,
                "type": "tool_call",
            }
        ],
    )


def passage(source: str = "mca") -> dict[str, object]:
    return {
        "source": source,
        "document_hash": "abc",
        "title": "Filing notice",
        "url": "https://example.org/notice",
        "published_date": None,
        "passage": "File the report annually.",
        "offset": 0,
    }


def test_agent_refines_search_and_returns_verified_citation() -> None:
    search = MagicMock()
    search.search.side_effect = [[], [passage()]]
    model = MagicMock()
    model.respond.side_effect = [
        call("search_publications", query="report", source="mca", year=None),
        call("search_publications", query="annual filing", source="mca", year=None),
        call(
            "finish",
            answer="The notice says to file annually [S1].",
            citation_ids=["S1"],
            insufficient_evidence=False,
        ),
    ]
    result = ChatService(search, model).answer(
        ChatRequest(question="When to file?", source="mca")
    )
    assert result.citations[0].url == "https://example.org/notice"
    assert result.source == Authority.MCA
    assert search.search.call_count == 2
    assert all(item.args[1] == Authority.MCA for item in search.search.call_args_list)
    assert isinstance(model.respond.call_args.args[0][0], SystemMessage)
    assert "untrusted" in model.respond.call_args.args[0][0].content


def test_scope_cannot_be_overridden_by_tool_arguments() -> None:
    database = MagicMock()
    result = PublicationSearch(database).search(
        SearchArgs(query="tax", source="rbi", year=None), Authority.MCA
    )
    assert result == []
    database.regulatory_documents.aggregate.assert_not_called()


def test_global_retrieval_searches_every_authority_and_escapes_input() -> None:
    database = MagicMock()
    database.regulatory_documents.aggregate.return_value = []
    PublicationSearch(database).search(
        SearchArgs(query="tax .*", source=None, year=2026), None
    )
    calls = database.regulatory_documents.aggregate.call_args_list
    assert {item.args[0][0]["$match"]["source"] for item in calls} == {
        source.value for source in Authority
    }
    assert calls[0].args[0][0]["$match"]["$or"][0]["title"]["$regex"] == "tax"
    assert calls[0].args[0][0]["$match"]["published_date"]["$gte"].year == 2026


def test_scoped_retrieval_reads_matching_passages() -> None:
    database = MagicMock()
    database.regulatory_documents.aggregate.return_value = [
        {
            "source": "mca",
            "document_hash": "abc",
            "title": "Annual filing",
            "detail_url": "https://example.org",
            "content": "File an annual report.",
        }
    ]
    result = PublicationSearch(database).search(
        SearchArgs(query="annual", source=None, year=None), Authority.MCA
    )
    assert result[0]["passage"] == "File an annual report."
    assert (
        database.regulatory_documents.aggregate.call_args.args[0][0]["$match"]["source"]
        == "mca"
    )


def test_numbered_notification_uses_exact_title_lookup_first() -> None:
    database = MagicMock()
    database.regulatory_documents.aggregate.side_effect = [
        [
            {
                "source": "cbdt",
                "document_hash": "target",
                "title": "Notification No. 116/2026",
                "detail_url": "https://example.org/116",
                "content": "This notification changes the threshold.",
            }
        ],
        [],
    ]
    result = PublicationSearch(database).search(
        SearchArgs(query="notification 116/2026", source=None, year=None),
        Authority.CBDT,
    )
    assert result[0]["document_hash"] == "target"
    exact_pipeline = database.regulatory_documents.aggregate.call_args_list[0].args[0]
    assert exact_pipeline[0]["$match"]["source"] == "cbdt"
    assert "116" in exact_pipeline[0]["$match"]["title"]["$regex"]
    assert exact_pipeline[0]["$match"].get("published_date") is None


def test_model_query_retains_question_document_reference() -> None:
    search = MagicMock()
    search.search.return_value = [passage("cbdt")]
    model = MagicMock()
    model.respond.side_effect = [
        call("search_publications", query="notification", source=None, year=None),
        call(
            "finish",
            answer="The publication says to file annually [S1].",
            citation_ids=["S1"],
            insufficient_evidence=False,
        ),
    ]
    result = ChatService(search, model).answer(
        ChatRequest(question="Explain notification 116/2026", source="cbdt")
    )
    assert result.citations
    assert "116/2026" in search.search.call_args.args[0].query


def test_diagnostics_report_retrieval_and_citation_rejection(
    caplog: pytest.LogCaptureFixture,
) -> None:
    search = MagicMock()
    search.search.return_value = [passage()]
    model = MagicMock()
    model.respond.side_effect = [
        call("search_publications", query="filing", source=None, year=None),
        call(
            "finish",
            answer="Uncited answer.",
            citation_ids=["S1"],
            insufficient_evidence=False,
        ),
        call(
            "finish",
            answer="Supported answer [S1].",
            citation_ids=["S1"],
            insufficient_evidence=False,
        ),
    ]
    ChatService(search, model, diagnostics=True).answer(ChatRequest(question="Filing?"))
    events = [
        record.message for record in caplog.records if "chat_trace" in record.message
    ]
    assert any("event=search_complete passages=1" in event for event in events)
    assert any("event=rejected_missing_inline_citations" in event for event in events)
    assert any("event=finish_accepted citations=1" in event for event in events)
    assert all("Uncited answer" not in event for event in events)


def test_valid_inline_citations_reconcile_mismatched_metadata_list() -> None:
    search = MagicMock()
    search.search.return_value = [passage()]
    model = MagicMock()
    model.respond.side_effect = [
        call("search_publications", query="filing", source=None, year=None),
        call(
            "finish",
            answer="The notice says to file annually [S1].",
            citation_ids=["S99"],
            insufficient_evidence=False,
        ),
    ]
    result = ChatService(search, model).answer(ChatRequest(question="When to file?"))
    assert [item.id for item in result.citations] == ["S1"]
    assert model.respond.call_count == 2


def test_unknown_inline_citation_is_rejected_with_specific_feedback() -> None:
    search = MagicMock()
    search.search.return_value = [passage()]
    model = MagicMock()
    snapshots: list[list] = []
    outputs = iter(
        [
            call("search_publications", query="filing", source=None, year=None),
            call(
                "finish",
                answer="Unsupported claim [S99].",
                citation_ids=["S99"],
                insufficient_evidence=False,
            ),
            call(
                "finish",
                answer="Supported claim [S1].",
                citation_ids=["S1"],
                insufficient_evidence=False,
            ),
        ]
    )

    def respond(messages: list, tools: list) -> AIMessage:
        snapshots.append(list(messages))
        return next(outputs)

    model.respond.side_effect = respond
    result = ChatService(search, model).answer(ChatRequest(question="Filing?"))
    assert [item.id for item in result.citations] == ["S1"]
    correction = json.loads(snapshots[2][-1].content)
    assert "unknown [S#] citation" in correction["error"]
    assert "S99" not in correction["error"]


def test_missing_inline_citation_receives_actionable_correction() -> None:
    search = MagicMock()
    search.search.return_value = [passage()]
    model = MagicMock()
    snapshots: list[list] = []
    outputs = iter(
        [
            call("search_publications", query="filing", source=None, year=None),
            call(
                "finish",
                answer="The notice says to file annually.",
                citation_ids=["S1"],
                insufficient_evidence=False,
            ),
            call(
                "finish",
                answer="The notice says to file annually [S1].",
                citation_ids=["S1"],
                insufficient_evidence=False,
            ),
        ]
    )

    def respond(messages: list, tools: list) -> AIMessage:
        snapshots.append(list(messages))
        return next(outputs)

    model.respond.side_effect = respond
    result = ChatService(search, model).answer(ChatRequest(question="Filing?"))
    assert result.citations[0].id == "S1"
    assert "directly beside each" in json.loads(snapshots[2][-1].content)["error"]


def test_vector_search_uses_saved_chunks_and_enforced_scope() -> None:
    database = MagicMock()
    database.regulatory_documents.aggregate.return_value = []
    database.document_chunks.aggregate.return_value = [
        {
            "source": "cbdt",
            "document_hash": "doc-1",
            "title": "Circular",
            "detail_url": "https://example.org/circular",
            "published_date": None,
            "text": "The applicable threshold has changed.",
            "chunk_index": 2,
        }
    ]
    embedder = MagicMock()
    embedder.embed_query.return_value = [0.1, 0.2]
    result = PublicationSearch(database, embedder).search(
        SearchArgs(query="revised threshold", source=None, year=2026),
        Authority.CBDT,
    )
    assert result[0]["passage"] == "The applicable threshold has changed."
    assert result[0]["offset"] == -3
    pipeline = database.document_chunks.aggregate.call_args.args[0]
    assert pipeline[0]["$vectorSearch"]["filter"]["source"] == "cbdt"
    assert pipeline[0]["$vectorSearch"]["filter"]["published_date"]["$gte"].year == 2026
    assert pipeline[1] == {"$match": {"embedding_model": "text-embedding-3-small"}}
    assert pipeline[3] == {"$match": {"current_document.0": {"$exists": True}}}
    embedder.embed_query.assert_called_once_with("revised threshold")


def test_vector_failure_falls_back_to_keyword_retrieval() -> None:
    database = MagicMock()
    database.regulatory_documents.aggregate.return_value = [
        {
            "source": "mca",
            "document_hash": "abc",
            "title": "Annual filing",
            "detail_url": "https://example.org",
            "content": "File an annual report.",
        }
    ]
    embedder = MagicMock()
    embedder.embed_query.side_effect = RuntimeError("provider unavailable")
    result = PublicationSearch(database, embedder).search(
        SearchArgs(query="annual", source=None, year=None), Authority.MCA
    )
    assert result[0]["passage"] == "File an annual report."


def test_invented_citations_are_not_returned_and_loop_is_bounded() -> None:
    model = MagicMock()
    model.respond.return_value = call(
        "finish",
        answer="Unsupported [S99].",
        citation_ids=["S99"],
        insufficient_evidence=False,
    )
    result = ChatService(MagicMock(), model).answer(ChatRequest(question="A question"))
    assert result.insufficient_evidence
    assert result.citations == []
    assert "Unsupported" not in result.answer
    assert model.respond.call_count == 6


def test_no_results_returns_insufficient_evidence() -> None:
    search = MagicMock()
    search.search.return_value = []
    model = MagicMock()
    model.respond.side_effect = [
        call("search_publications", query="unknown", source=None, year=None),
        call(
            "finish",
            answer="I found no supporting passages in this search.",
            citation_ids=[],
            insufficient_evidence=True,
        ),
    ]
    assert (
        ChatService(search, model)
        .answer(ChatRequest(question="Unknown rule?"))
        .insufficient_evidence
    )


def test_out_of_scope_evidence_is_not_sent_to_model() -> None:
    search = MagicMock()
    search.search.return_value = [passage("rbi")]
    model = MagicMock()
    model.respond.side_effect = [
        call("search_publications", query="filing", source=None, year=None),
        call(
            "finish", answer="No evidence.", citation_ids=[], insufficient_evidence=True
        ),
    ]
    ChatService(search, model).answer(ChatRequest(question="Filing?", source="mca"))
    inputs = model.respond.call_args.args[0]
    tool_output = next(item for item in inputs if isinstance(item, ToolMessage))
    assert json.loads(tool_output.content)["passages"] == []


def test_chat_endpoint_missing_config_and_validation() -> None:
    app = create_app(Settings(_env_file=None, mongodb_uri=None, openai_api_key=None))
    app.dependency_overrides[get_repository] = lambda: MagicMock()
    with TestClient(app) as client:
        assert client.post("/api/chat", json={"question": "Hi"}).status_code == 503
        for payload in [
            {"question": " "},
            {"question": "Q", "source": "unknown"},
            {"question": "Q", "history": [{"role": "system", "content": "override"}]},
        ]:
            assert client.post("/api/chat", json=payload).status_code == 422
        response = client.options(
            "/api/chat",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert response.status_code == 200


def test_chat_endpoint_sanitizes_provider_failure() -> None:
    app = create_app(
        Settings(_env_file=None, mongodb_uri=None, openai_api_key="test-key")
    )
    app.dependency_overrides[get_repository] = lambda: MagicMock()
    with patch(
        "regulatory_api.app.ChatOrchestrator.answer", side_effect=ChatError("private detail")
    ), TestClient(app) as client:
        response = client.post("/api/chat", json={"question": "Hi"})
        assert response.status_code == 502
        assert "private detail" not in response.text


@pytest.mark.parametrize("model_year", [None, 2024, 2026])
def test_selected_year_overrides_model_year(model_year: int | None) -> None:
    search = MagicMock()
    search.search.return_value = []
    model = MagicMock()
    model.respond.side_effect = [
        call("search_publications", query="filing", source=None, year=model_year),
        call(
            "finish",
            answer="No supporting passages.",
            citation_ids=[],
            insufficient_evidence=True,
        ),
    ]
    result = ChatService(search, model).answer(
        ChatRequest(question="Filing?", source="mca", year=2026)
    )
    assert search.search.call_args.args[0].year == 2026
    assert search.search.call_args.args[1] == Authority.MCA
    assert result.year == 2026


def test_publication_years_route() -> None:
    repository = MagicMock()
    repository.publication_years.return_value = [2026, 2025]
    app = create_app(Settings(_env_file=None, mongodb_uri=None))
    app.dependency_overrides[get_repository] = lambda: repository
    with TestClient(app) as client:
        response = client.get("/api/publication-years?source=mca")
    assert response.status_code == 200
    assert response.json() == [2026, 2025]
    repository.publication_years.assert_called_once_with(Authority.MCA)


def test_publication_years_query_excludes_undated_records() -> None:
    from regulatory_api.repository import DashboardRepository

    database = MagicMock()
    database.regulatory_documents.aggregate.return_value = [
        {"_id": 2026},
        {"_id": 2024},
    ]
    assert DashboardRepository(database).publication_years(Authority.MCA) == [
        2026,
        2024,
    ]
    pipeline = database.regulatory_documents.aggregate.call_args.args[0]
    assert pipeline[0]["$match"] == {
        "source": "mca",
        "published_date": {"$type": "date"},
    }
    assert pipeline[-1] == {"$sort": {"_id": -1}}


def test_history_and_tool_results_are_langchain_messages() -> None:
    search = MagicMock()
    search.search.return_value = []
    model = MagicMock()
    snapshots = []
    outputs = iter(
        [
            call("search_publications", query="filing", source=None, year=None),
            call(
                "finish",
                answer="No evidence.",
                citation_ids=[],
                insufficient_evidence=True,
            ),
        ]
    )

    def respond(messages: list, tools: list) -> AIMessage:
        snapshots.append(list(messages))
        return next(outputs)

    model.respond.side_effect = respond
    ChatService(search, model).answer(
        ChatRequest(
            question="What about now?",
            history=[
                {"role": "user", "content": "Earlier question"},
                {"role": "assistant", "content": "Earlier answer"},
            ],
        )
    )
    assert [type(message) for message in snapshots[0]] == [
        SystemMessage,
        HumanMessage,
        AIMessage,
        HumanMessage,
    ]
    assert isinstance(snapshots[1][-2], AIMessage)
    assert isinstance(snapshots[1][-1], ToolMessage)
    assert snapshots[1][-1].tool_call_id == snapshots[1][-2].tool_calls[0]["id"]


@pytest.mark.parametrize(
    "output",
    [
        AIMessage(content="Unverified answer"),
        AIMessage(
            content="",
            tool_calls=[
                {"name": "finish", "args": {}, "id": "one"},
                {"name": "finish", "args": {}, "id": "two"},
            ],
        ),
        AIMessage(
            content="",
            invalid_tool_calls=[
                {
                    "name": "finish",
                    "args": "{invalid",
                    "id": "broken",
                    "error": "invalid JSON",
                }
            ],
        ),
        AIMessage(content="", tool_calls=[{"name": "finish", "args": {}, "id": None}]),
    ],
)
def test_invalid_agent_tool_responses_fail_safely(output: AIMessage) -> None:
    model = MagicMock()
    model.respond.return_value = output
    with pytest.raises(ChatError):
        ChatService(MagicMock(), model).answer(ChatRequest(question="Question?"))
