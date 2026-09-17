import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from threading import BoundedSemaphore
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from .chat import (
    ChatError,
    ChatRequest,
    ChatResponse,
    ChatService,
    PublicationSearch,
)
from .embeddings import QueryEmbedder
from .llm import LangChainClient
from .models import Authority, DocumentDetail, DocumentPage, Overview
from .qa.orchestrator import ChatOrchestrator
from .regulatory_intelligence.service import IntelligenceService
from .repository import DashboardRepository

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    openai_api_key: SecretStr | None = None
    chat_model: str = "gpt-4.1"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    vector_index_name: str = "document_chunks_vector"
    chat_diagnostics_enabled: bool = False
    chat_multi_agent_enabled: bool = True
    mongodb_uri: str | None = None
    mongodb_database: str = "taxwatch"
    phase3_api_token: SecretStr | None = None
    phase3_tenant_id: str | None = None
    api_cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]


def get_repository(request: Request) -> DashboardRepository:
    repository = request.app.state.repository
    if repository is None:
        raise HTTPException(503, "Database is not configured")
    return repository


Repository = Annotated[DashboardRepository, Depends(get_repository)]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    chat_slots = BoundedSemaphore(2)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        client = None
        application.state.repository = None
        if settings.mongodb_uri:
            client = MongoClient(
                settings.mongodb_uri,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=5000,
                socketTimeoutMS=10000,
                timeoutMS=15000,
                tz_aware=True,
            )
            application.state.repository = DashboardRepository(
                client[settings.mongodb_database]
            )
        try:
            yield
        finally:
            if client is not None:
                client.close()

    application = FastAPI(title="TaxWatch Regulatory API", lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api_cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Accept", "Content-Type", "Authorization"],
    )

    @application.exception_handler(PyMongoError)
    async def database_error(request: Request, exc: PyMongoError) -> JSONResponse:
        # Avoid leaking connection strings or server details to logs/responses.
        logger.warning("Database request failed: %s", type(exc).__name__)
        return JSONResponse(status_code=503, content={"detail": "Database unavailable"})

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/api/overview", response_model=Overview)
    def overview(
        repository: Repository,
        year: Annotated[int | None, Query(ge=1900, le=9998)] = None,
    ) -> Overview:
        return repository.overview(year=year)

    @application.get("/api/documents", response_model=DocumentPage)
    def documents(
        repository: Repository,
        source: Authority | None = None,
        q: Annotated[str, Query(max_length=200)] = "",
        year: Annotated[int | None, Query(ge=1900, le=9998)] = None,
        page: Annotated[int, Query(ge=1, le=100000)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> DocumentPage:
        return repository.documents(source, q, year, page, page_size)

    @application.get(
        "/api/documents/{source}/{document_hash}", response_model=DocumentDetail
    )
    def detail(
        source: Authority, document_hash: str, repository: Repository
    ) -> DocumentDetail:
        record = repository.detail(source, document_hash)
        if record is None:
            raise HTTPException(404, "Document not found")
        return DocumentDetail.model_validate(record)

    @application.get("/api/publication-years", response_model=list[int])
    def publication_years(
        repository: Repository, source: Authority | None = None
    ) -> list[int]:
        return repository.publication_years(source)

    @application.get("/api/documents/{source}/{document_hash}/intelligence")
    def intelligence(source: Authority, document_hash: str, repository: Repository):
        return {
            "intelligence": IntelligenceService(repository.database).get_intelligence(
                source.value, document_hash
            )
        }

    @application.get("/api/client-alerts")
    def client_alerts(request: Request, repository: Repository):
        # Single-tenant MVP credential; tenant never comes from query parameters.
        if not settings.phase3_api_token or not settings.phase3_tenant_id:
            raise HTTPException(503, "Private alerts are not configured")
        expected = "Bearer " + settings.phase3_api_token.get_secret_value()
        supplied = request.headers.get("Authorization", "")
        if not secrets.compare_digest(supplied.encode(), expected.encode()):
            raise HTTPException(401, "Authentication required")
        return {
            "items": IntelligenceService(repository.database).alerts(
                settings.phase3_tenant_id
            )
        }

    @application.post("/api/chat", response_model=ChatResponse)
    def chat(payload: ChatRequest, repository: Repository) -> ChatResponse:
        if (
            not settings.openai_api_key
            or not settings.openai_api_key.get_secret_value()
        ):
            raise HTTPException(
                503, "Chat is not configured. Set OPENAI_API_KEY on the API server."
            )
        if not chat_slots.acquire(blocking=False):
            raise HTTPException(429, "Chat is busy. Please try again shortly.")
        try:
            service_class = (
                ChatOrchestrator if settings.chat_multi_agent_enabled else ChatService
            )
            service = service_class(
                PublicationSearch(
                    repository.database,
                    QueryEmbedder(
                        settings.openai_api_key.get_secret_value(),
                        settings.embedding_model,
                        settings.embedding_dimensions,
                    ),
                    settings.embedding_model,
                    settings.vector_index_name,
                ),
                LangChainClient(
                    settings.openai_api_key.get_secret_value(), settings.chat_model
                ),
                diagnostics=settings.chat_diagnostics_enabled,
            )
            return service.answer(payload)
        except ChatError as exc:
            logger.error("Chat request failed: %s", type(exc).__name__, exc_info=exc)
            raise HTTPException(
                502, "The answer service is unavailable. Please try again."
            ) from exc
        except Exception as exc:
            logger.exception(
                "Unexpected chat request failure: %s", type(exc).__name__, exc_info=exc
            )
            raise HTTPException(
                502, "The answer service is unavailable. Please try again."
            ) from exc
        finally:
            chat_slots.release()

    return application
