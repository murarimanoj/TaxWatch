"""LangChain model boundary, independent of retrieval and HTTP routes."""

from typing import Any, Protocol

from langchain_core.messages import AIMessage, BaseMessage
from langchain_openai import ChatOpenAI
from openai import OpenAIError
from pydantic import SecretStr


class ChatError(Exception):
    """The model could not complete a usable chat response."""


class ModelClient(Protocol):
    def respond(
        self, messages: list[BaseMessage], tools: list[dict[str, Any]]
    ) -> AIMessage: ...


class LangChainClient:
    def __init__(self, key: str, model: str) -> None:
        self.model = ChatOpenAI(
            api_key=SecretStr(key),
            model=model,
            use_responses_api=True,
            output_version="responses/v1",
            store=False,
            timeout=30,
            max_retries=0,
            max_tokens=2500,
        )

    def respond(
        self, messages: list[BaseMessage], tools: list[dict[str, Any]]
    ) -> AIMessage:
        try:
            response = self.model.bind_tools(
                tools,
                tool_choice="required",
                strict=True,
                parallel_tool_calls=False,
            ).invoke(messages)
        except (OpenAIError, ValueError) as exc:
            raise ChatError("Model service unavailable") from exc
        if not isinstance(response, AIMessage) or response.invalid_tool_calls:
            raise ChatError("Invalid model response")
        if response.response_metadata.get("finish_reason") in (
            "length",
            "content_filter",
        ):
            raise ChatError("Incomplete model response")
        if response.response_metadata.get("status") in (
            "incomplete",
            "failed",
            "cancelled",
        ):
            raise ChatError("Incomplete model response")
        return response
