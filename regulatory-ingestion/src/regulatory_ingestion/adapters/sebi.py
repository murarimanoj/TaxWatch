from ..config import SourcePageConfig
from ..domain import BaseRegulatoryDocument, Source
from .base import HtmlListingAdapter


class SebiAdapter(HtmlListingAdapter):
    source = Source.SEBI
    def parse_listing(self, html: str, limit: int,
                      page: SourcePageConfig | None = None) -> list[BaseRegulatoryDocument]:
        page = page or SourcePageConfig(
            url="https://example.test/sebi", document_type="notification"
        )
        soup = self.soup(html)
        results: list[BaseRegulatoryDocument] = []
        selectors = (
            "table a[href], .fixed-table-body a[href], a[href*='/legal/circulars/'], "
            "a[href*='/media-and-notifications/']"
        )
        for link in soup.select(selectors):
            title = link.get_text(" ", strip=True)
            if len(title) < 8:
                continue
            row = link.find_parent("tr")
            cells = row.find_all("td") if row else []
            date_text = cells[0].get_text(" ", strip=True) if cells else None
            results.append(
                self.candidate(title=title, href=link.get("href", ""), date_text=date_text,
                               document_type=page.document_type, base_url=str(page.url),
                               transport=page.transport)
            )
            if len(results) >= limit:
                break
        return results
