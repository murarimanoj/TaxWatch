import re
from datetime import date
from time import strptime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..browser import BrowserClient
from ..config import SourcePageConfig
from ..domain import BaseRegulatoryDocument, Source
from ..http import HttpClient


class HtmlListingAdapter:
    source: Source
    date_formats = (
        "%b %d, %Y",
        "%B %d, %Y",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%d.%m.%Y",
        "%d %b, %Y",
        "%d %B, %Y",
        "%d-%b-%Y",
        "%d %b %Y",
        "%d %B %Y",
    )

    def __init__(
        self, client: HttpClient, pages: list[SourcePageConfig], browser: BrowserClient | None = None
    ) -> None:
        self.client = client
        self.pages = pages
        self.browser = browser

    def discover(self, limit: int) -> list[BaseRegulatoryDocument]:
        results: list[BaseRegulatoryDocument] = []
        for page in self.pages:
            if page.transport == "playwright":
                if self.browser is None:
                    raise RuntimeError("Playwright transport is configured but unavailable")
                response = self.browser.get(str(page.url))
            else:
                response = self.client.get(str(page.url))
            results.extend(self.parse_listing(response.text, limit, page))
        return results

    def parse_listing(
        self, html: str, limit: int, page: SourcePageConfig | None = None
    ) -> list[BaseRegulatoryDocument]:
        raise NotImplementedError

    def candidate(
        self, *, title: str, href: str, date_text: str | None,
        document_type: str, base_url: str, transport: str = "http"
    ) -> BaseRegulatoryDocument:
        absolute_url = urljoin(base_url, href)
        normalized_title = " ".join(title.split())
        return BaseRegulatoryDocument(
            source=self.source,
            title=normalized_title,
            published_date=self.parse_date(date_text),
            document_type=document_type,
            detail_url=absolute_url,
            attachment_url=absolute_url if absolute_url.lower().endswith(".pdf") else None,
            metadata={"transport": transport},
        )

    def parse_date(self, value: str | None) -> date | None:
        if not value:
            return None
        cleaned = re.sub(r"(?<=\d)(st|nd|rd|th)\b", "", value, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        for fmt in self.date_formats:
            try:
                parsed = strptime(cleaned, fmt)
                return date(parsed.tm_year, parsed.tm_mon, parsed.tm_mday)
            except ValueError:
                continue
        return None

    @staticmethod
    def soup(html: str) -> BeautifulSoup:
        return BeautifulSoup(html, "html.parser")
