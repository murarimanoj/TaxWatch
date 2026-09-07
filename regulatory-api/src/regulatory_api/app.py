import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic_settings import BaseSettings, SettingsConfigDict
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from .models import Authority, DocumentDetail, DocumentPage, Overview
from .repository import DashboardRepository

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    mongodb_uri: str | None = None
    mongodb_database: str = "taxwatch"
    api_cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]


def get_repository(request: Request) -> DashboardRepository:
    repository = request.app.state.repository
    if repository is None:
        raise HTTPException(503, "Database is not configured")
    return repository


Repository = Annotated[DashboardRepository, Depends(get_repository)]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

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
        allow_methods=["GET"],
        allow_headers=["Accept"],
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
    def overview(repository: Repository) -> Overview:
        return repository.overview()

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

    return application
