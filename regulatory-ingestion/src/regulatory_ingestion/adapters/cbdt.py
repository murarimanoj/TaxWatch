from ..config import SourcePageConfig
from ..domain import BaseRegulatoryDocument, Source
from .base import HtmlListingAdapter


class CbdtAdapter(HtmlListingAdapter):
    source = Source.CBDT

    def discover(self, limit: int) -> list[BaseRegulatoryDocument]:
        results: list[BaseRegulatoryDocument] = []
        for page in self.pages:
            if page.transport == "playwright":
                if self.browser is None:
                    raise RuntimeError("Playwright transport is configured but unavailable")
                response = self.browser.get_with_popup_links(
                    str(page.url), "button.card-title[role='link']", limit
                )
            else:
                response = self.client.get(str(page.url))
            results.extend(self.parse_listing(response.text, limit, page))
        return results

    def parse_listing(self, html: str, limit: int,
                      page: SourcePageConfig | None = None) -> list[BaseRegulatoryDocument]:
        page = page or SourcePageConfig(
            url="https://example.test/cbdt", document_type="notification"
        )
        soup = self.soup(html)
        results: list[BaseRegulatoryDocument] = []
        for link in soup.select("button.card-title[data-document-url]"):
            title = link.get("title", "") or link.get_text(" ", strip=True)
            if len(title) < 8:
                continue
            container = link.find_parent("div", class_="card-body")
            date_node = container.select_one(".published-text + span") if container else None
            date_text = date_node.get_text(" ", strip=True) if date_node else None
            results.append(
                self.candidate(title=title, href=link.get("data-document-url", ""),
                               date_text=date_text,
                               document_type=page.document_type, base_url=str(page.url),
                               transport=page.transport)
            )
            if len(results) >= limit:
                break
        return results
