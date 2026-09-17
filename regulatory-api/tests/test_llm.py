"""Exercise the real LangChain/OpenAI adapter with an in-memory HTTP transport."""

import json
from unittest.mock import patch

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

from regulatory_api.chat import TOOLS
from regulatory_api.llm import ChatError, LangChainClient


def test_langchain_responses_request_and_tool_parsing() -> None:
    requests: list[dict] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": "gpt-4.1",
                "error": None,
                "incomplete_details": None,
                "output": [
                    {
                        "id": "fc_test",
                        "type": "function_call",
                        "call_id": "call_test",
                        "name": "search_publications",
                        "arguments": json.dumps(
                            {"query": "filing", "source": "mca", "year": 2026}
                        ),
                        "status": "completed",
                    }
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as transport:

        def make_model(**kwargs: object) -> ChatOpenAI:
            return ChatOpenAI(**kwargs, http_client=transport)

        with patch("regulatory_api.llm.ChatOpenAI", side_effect=make_model):
            client = LangChainClient("test-key", "gpt-4.1")
        response = client.respond(
            [
                SystemMessage(content="Server-only instructions"),
                HumanMessage(content="What must I file?"),
            ],
            TOOLS,
        )
        client.respond(
            [
                SystemMessage(content="Server-only instructions"),
                HumanMessage(content="What must I file?"),
                response,
                ToolMessage(content='{"passages": []}', tool_call_id="call_test"),
            ],
            TOOLS,
        )
    next_input = requests[1]["input"]
    tool_result = next(
        item for item in next_input if item.get("type") == "function_call_output"
    )
    assert tool_result["call_id"] == "call_test"
    assert json.loads(tool_result["output"]) == {"passages": []}
    assert isinstance(response, AIMessage)
    assert response.tool_calls[0]["id"] == "call_test"
    assert response.tool_calls[0]["args"]["year"] == 2026
    body = requests[0]
    assert body["store"] is False
    assert body["parallel_tool_calls"] is False
    assert body["tool_choice"] == "required"
    assert body["max_output_tokens"] == 2500
    assert {item["name"] for item in body["tools"]} == {"search_publications", "finish"}
    assert all(item["strict"] for item in body["tools"])
    assert body["input"][0]["role"] == "system"


@pytest.mark.parametrize("status", [401, 429, 500])
def test_provider_errors_are_translated_after_bounded_retries(status: int) -> None:
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            status,
            headers={"retry-after-ms": "1"},
            json={
                "error": {
                    "message": "private detail",
                    "type": "provider_error",
                    "code": None,
                }
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as transport:

        def make_model(**kwargs: object) -> ChatOpenAI:
            return ChatOpenAI(**kwargs, http_client=transport)

        with patch("regulatory_api.llm.ChatOpenAI", side_effect=make_model):
            client = LangChainClient("test-key", "gpt-4.1")
        with pytest.raises(ChatError, match="Model service unavailable") as error:
            client.respond([HumanMessage(content="Question")], TOOLS)
    assert "private detail" not in str(error.value)
    assert error.value.__cause__ is not None
    assert len(requests) == (1 if status == 401 else 4)


@pytest.mark.parametrize(
    "response",
    [
        AIMessage(
            content="",
            invalid_tool_calls=[
                {"name": "finish", "args": "{bad", "id": "call", "error": "invalid"}
            ],
        ),
        AIMessage(content="partial", response_metadata={"finish_reason": "length"}),
        AIMessage(content="partial", response_metadata={"status": "incomplete"}),
    ],
)
def test_rejects_malformed_or_incomplete_output(response: AIMessage) -> None:
    with patch("regulatory_api.llm.ChatOpenAI") as factory:
        factory.return_value.bind_tools.return_value.invoke.return_value = response
        client = LangChainClient("test-key", "gpt-4.1")
        with pytest.raises(ChatError):
            client.respond([HumanMessage(content="Question")], TOOLS)


def test_final_step_only_binds_finish_tool() -> None:
    with patch("regulatory_api.llm.ChatOpenAI") as factory:
        factory.return_value.bind_tools.return_value.invoke.return_value = AIMessage(
            content=""
        )
        client = LangChainClient("test-key", "gpt-4.1")
        client.respond([HumanMessage(content="Question")], [TOOLS[1]])
        assert factory.return_value.bind_tools.call_args.args[0] == [TOOLS[1]]


def test_rate_limit_recovers_on_retry() -> None:
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                429,
                headers={"retry-after-ms": "1"},
                json={
                    "error": {
                        "message": "Temporary token limit",
                        "type": "tokens",
                        "code": "rate_limit_exceeded",
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "resp_retry",
                "object": "response",
                "created_at": 1,
                "model": "gpt-4.1",
                "status": "completed",
                "output": [
                    {
                        "id": "fc_retry",
                        "type": "function_call",
                        "call_id": "call_retry",
                        "name": "finish",
                        "arguments": json.dumps({"answer": "Recovered"}),
                        "status": "completed",
                    }
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as transport:

        def make_model(**kwargs: object) -> ChatOpenAI:
            return ChatOpenAI(**kwargs, http_client=transport)

        with patch("regulatory_api.llm.ChatOpenAI", side_effect=make_model):
            client = LangChainClient("test-key", "gpt-4.1")
        response = client.respond([HumanMessage(content="Question")], TOOLS)
    assert len(requests) == 2
    assert response.tool_calls[0]["args"] == {"answer": "Recovered"}
