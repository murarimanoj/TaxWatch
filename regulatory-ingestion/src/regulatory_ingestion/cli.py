import logging
from typing import Annotated

import typer

from .browser import BrowserClient
from .config import get_settings, load_source_catalog
from .domain import Source
from .embeddings import OpenAIEmbedder
from .extraction import ContentExtractor
from .http import HttpClient
from .pipeline import IngestionPipeline
from .processing import DocumentChunkProcessor, backfill_chunks
from .registry import build_adapter, select_source_pages
from .repository import (
    MongoDocumentRepository,
    NullDocumentRepository,
    configured_sources,
)

app = typer.Typer(help="Batch ingestion for Indian regulatory publications.")


def execute(
    source: Source,
    limit: int | None,
    dry_run: bool,
    document_type: str | None = None,
) -> None:
    settings = get_settings()
    catalog = load_source_catalog(settings.source_config_path)
    actual_limit = limit or settings.max_items_per_run
    client = HttpClient(settings.http_timeout_seconds, settings.http_user_agent)
    try:
        pages = select_source_pages(source, catalog, document_type)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--document-type") from exc
    needs_browser = any(page.transport == "playwright" for page in pages)
    browser_timeout = max(
        (
            page.timeout_seconds or settings.http_timeout_seconds
            for page in pages
            if page.transport == "playwright"
        ),
        default=settings.http_timeout_seconds,
    )
    browser = (
        BrowserClient(
            browser_timeout,
            settings.playwright_user_agent,
            settings.playwright_headless,
            settings.playwright_channel,
            settings.playwright_popup_timeout_seconds,
        )
        if needs_browser
        else None
    )
    if dry_run:
        repository = NullDocumentRepository()
    else:
        if not settings.mongodb_uri:
            raise typer.BadParameter("MONGODB_URI is required unless --dry-run is used")
        repository = MongoDocumentRepository(
            settings.mongodb_uri, settings.mongodb_database
        )
    if settings.embeddings_enabled and not settings.openai_api_key:
        raise typer.BadParameter(
            "OPENAI_API_KEY is required when EMBEDDINGS_ENABLED=true"
        )
    embedder = (
        OpenAIEmbedder(
            settings.openai_api_key,
            settings.embedding_model,
            settings.embedding_dimensions,
        )
        if settings.embeddings_enabled and settings.openai_api_key
        else None
    )
    try:
        summary = IngestionPipeline(
            build_adapter(source, client, catalog, browser, pages),
            client,
            ContentExtractor(),
            repository,
            browser,
            embedder,
            settings.chunk_size_chars,
            settings.chunk_overlap_chars,
        ).run(actual_limit)
    finally:
        if embedder is not None:
            embedder.close()
        if browser is not None:
            browser.close()
        client.close()
    typer.echo(summary.model_dump_json(indent=2))
    if summary.failed:
        raise typer.Exit(code=1)


@app.command("run")
def run_source(
    source: Annotated[Source, typer.Argument(help="One of: rbi, cbdt, sebi, gst, mca")],
    limit: Annotated[int | None, typer.Option(min=1, max=500)] = None,
    document_type: Annotated[
        str | None,
        typer.Option(
            "--document-type",
            "-t",
            help="Process only pages with this configured document type",
        ),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option(help="Fetch/extract without MongoDB writes")
    ] = False,
) -> None:
    execute(source, limit, dry_run, document_type)


@app.command("run-all")
def run_all(
    limit: Annotated[int | None, typer.Option(min=1, max=500)] = None,
    dry_run: Annotated[bool, typer.Option()] = False,
) -> None:
    failed = False
    for source in Source:
        try:
            execute(source, limit, dry_run)
        except typer.Exit:
            failed = True
    if failed:
        raise typer.Exit(code=1)


@app.command("sources")
def sources() -> None:
    for source in Source:
        typer.echo(source.value)


@app.command("init-db")
def init_db() -> None:
    """Create indexes and synchronize configured regulator sources without ingestion."""
    settings = get_settings()
    if not settings.mongodb_uri:
        raise typer.BadParameter("MONGODB_URI is required")
    catalog = load_source_catalog(settings.source_config_path)
    repository = MongoDocumentRepository(
        settings.mongodb_uri, settings.mongodb_database
    )
    repository.ensure_indexes()
    records = configured_sources(catalog)
    repository.sync_sources(records)
    typer.echo(f"Synchronized {len(records)} records into regulatory_sources")


@app.command("init-vector-index")
def init_vector_index() -> None:
    """Create the Atlas Vector Search index for document_chunks."""
    settings = get_settings()
    if not settings.mongodb_uri:
        raise typer.BadParameter("MONGODB_URI is required")
    repository = MongoDocumentRepository(
        settings.mongodb_uri, settings.mongodb_database
    )
    repository.ensure_chunk_indexes()
    repository.ensure_vector_index(settings.embedding_dimensions)
    typer.echo("Vector index document_chunks_vector requested")


@app.command("embed-stored")
def embed_stored(
    source: Annotated[
        Source | None, typer.Option(help="Only process one regulator")
    ] = None,
    limit: Annotated[int, typer.Option(min=1, max=10000)] = 100,
    force: Annotated[
        bool, typer.Option(help="Re-embed even when current chunks exist")
    ] = False,
) -> None:
    """Chunk and embed documents already stored in regulatory_documents."""
    settings = get_settings()
    if not settings.mongodb_uri:
        raise typer.BadParameter("MONGODB_URI is required")
    if not settings.openai_api_key:
        raise typer.BadParameter("OPENAI_API_KEY is required")
    repository = MongoDocumentRepository(
        settings.mongodb_uri, settings.mongodb_database
    )
    repository.ensure_chunk_indexes()
    embedder = OpenAIEmbedder(
        settings.openai_api_key,
        settings.embedding_model,
        settings.embedding_dimensions,
    )
    try:
        processor = DocumentChunkProcessor(
            repository,
            embedder,
            settings.chunk_size_chars,
            settings.chunk_overlap_chars,
        )
        summary = backfill_chunks(repository, processor, source, limit, force)
    finally:
        embedder.close()
    typer.echo(summary.model_dump_json(indent=2))
    if summary.failed:
        raise typer.Exit(code=1)

def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    app()


if __name__ == "__main__":
    main()
