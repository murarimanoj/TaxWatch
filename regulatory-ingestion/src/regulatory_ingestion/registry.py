from .adapters import CbdtAdapter, GstAdapter, McaAdapter, RbiAdapter, SebiAdapter
from .browser import BrowserClient
from .config import SourceCatalog, SourcePageConfig
from .domain import Source
from .http import HttpClient
from .ports import SourceAdapter


def select_source_pages(
    source: Source,
    catalog: SourceCatalog,
    document_type: str | None = None,
) -> list[SourcePageConfig]:
    pages = catalog.sources[source.value].pages
    if document_type is None:
        return pages

    selected_pages = [page for page in pages if page.document_type == document_type]
    if selected_pages:
        return selected_pages

    available_types = ", ".join(sorted({page.document_type for page in pages}))
    raise ValueError(
        f"Unknown document type {document_type!r} for {source.value}. "
        f"Available types: {available_types}"
    )


def build_adapter(
    source: Source,
    client: HttpClient,
    catalog: SourceCatalog,
    browser: BrowserClient | None = None,
    pages: list[SourcePageConfig] | None = None,
) -> SourceAdapter:
    adapters = {
        Source.RBI: RbiAdapter,
        Source.CBDT: CbdtAdapter,
        Source.SEBI: SebiAdapter,
        Source.GST: GstAdapter,
        Source.MCA: McaAdapter,
    }
    source_config = catalog.sources[source.value]
    adapter_type = adapters[source]
    if source_config.adapter != source.value:
        raise ValueError(
            f"Configured adapter {source_config.adapter!r} does not match source {source.value!r}"
        )
    selected_pages = source_config.pages if pages is None else pages
    return adapter_type(client, selected_pages, browser)
