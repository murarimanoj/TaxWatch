import logging
from typing import Annotated

import typer

from .browser import BrowserClient
from .config import get_settings, load_source_catalog
from .domain import Source
from .extraction import ContentExtractor
from .http import HttpClient
from .pipeline import IngestionPipeline
from .registry import build_adapter, select_source_pages
from .repository import MongoDocumentRepository, NullDocumentRepository, configured_sources

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
        raise typer.BadParameter(
            str(exc), param_hint="--document-type"
        ) from exc
    needs_browser = any(page.transport == "playwright" for page in pages)
    browser_timeout = max(
        (
            page.timeout_seconds or settings.http_timeout_seconds
            for page in pages
            if page.transport == "playwright"
        ),
        default=settings.http_timeout_seconds,
    )
    browser = BrowserClient(
        browser_timeout,
        settings.playwright_user_agent,
        settings.playwright_headless,
        settings.playwright_channel,
        settings.playwright_popup_timeout_seconds,
    ) if needs_browser else None
    if dry_run:
        repository = NullDocumentRepository()
    else:
        if not settings.mongodb_uri:
            raise typer.BadParameter("MONGODB_URI is required unless --dry-run is used")
        repository = MongoDocumentRepository(settings.mongodb_uri, settings.mongodb_database)
    try:
        summary = IngestionPipeline(
            build_adapter(source, client, catalog, browser, pages),
            client,
            ContentExtractor(),
            repository,
            browser,
        ).run(actual_limit)
    finally:
        if browser is not None:
            browser.close()
        client.close()
    typer.echo(summary.model_dump_json(indent=2))
    if summary.failed:
        raise typer.Exit(code=1)


@app.command("run")
def run_source(
    source: Annotated[
        Source, typer.Argument(help="One of: rbi, cbdt, sebi, gst, mca")
    ],
    limit: Annotated[int | None, typer.Option(min=1, max=500)] = None,
    document_type: Annotated[
        str | None,
        typer.Option(
            "--document-type",
            "-t",
            help="Process only pages with this configured document type",
        ),
    ] = None,
    dry_run: Annotated[bool, typer.Option(help="Fetch/extract without MongoDB writes")] = False,
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
    repository = MongoDocumentRepository(settings.mongodb_uri, settings.mongodb_database)
    repository.ensure_indexes()
    records = configured_sources(catalog)
    repository.sync_sources(records)
    typer.echo(f"Synchronized {len(records)} records into regulatory_sources")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    app()


if __name__ == "__main__":
    main()
