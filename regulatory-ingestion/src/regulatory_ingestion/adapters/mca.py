import re

from bs4 import Tag

from ..config import SourcePageConfig
from ..domain import BaseRegulatoryDocument, Source
from .base import HtmlListingAdapter


class McaAdapter(HtmlListingAdapter):
    source = Source.MCA

    def discover(self, limit: int) -> list[BaseRegulatoryDocument]:
        results: list[BaseRegulatoryDocument] = []
        for page in self.pages:
            if page.transport == "playwright":
                if self.browser is None:
                    raise RuntimeError("Playwright transport is configured but unavailable")
                response = self.browser.get_with_popup_links(
                    str(page.url),
                    "#notificationCircularTable a.notifications.dmslink",
                    limit,
                )
            else:
                response = self.client.get(str(page.url))
            results.extend(self.parse_listing(response.text, limit, page))
        return results

    def parse_listing(
        self,
        html: str,
        limit: int,
        page: SourcePageConfig | None = None,
    ) -> list[BaseRegulatoryDocument]:
        page = page or SourcePageConfig(
            url="https://example.test/mca", document_type="circular"
        )
        soup = self.soup(html)
        results: list[BaseRegulatoryDocument] = []
        for row in soup.select("#notificationCircularTable tbody tr"):
            cells = row.find_all("td")
            link = self._document_link(row)
            if link is None or not cells:
                continue

            date_text = self._date_text(cells)
            title = self._title(cells, date_text)
            if not title:
                continue

            results.append(
                self.candidate(
                    title=title,
                    href=link.get("data-document-url", "") or link.get("href", ""),
                    date_text=date_text,
                    document_type=page.document_type,
                    base_url=str(page.url),
                    transport=page.transport,
                )
            )
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def _document_link(row: Tag) -> Tag | None:
        for link in row.select("a[href]"):
            if link.get("data-document-url"):
                return link
            href = link.get("href", "")
            if re.search(r"getdocument|\.pdf(?:$|\?)", href, re.IGNORECASE):
                return link
        return None

    def _date_text(self, cells: list[Tag]) -> str | None:
        for cell in cells:
            text = cell.get_text(" ", strip=True)
            match = re.search(
                r"\b(?:\d{1,2}[./-]\d{1,2}[./-]\d{4}|"
                r"\d{1,2}[ -](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
                r"[a-z]*[ ,-]+\d{4})\b",
                text,
                flags=re.IGNORECASE,
            )
            if match is not None:
                return match.group(0)
        return None

    @staticmethod
    def _title(cells: list[Tag], date_text: str | None) -> str:
        ignored = {"view", "download", "pdf", "open"}
        candidates: list[str] = []
        for cell in cells:
            text = " ".join(cell.get_text(" ", strip=True).split()).rstrip(" |")
            if (
                not text
                or text == date_text
                or text.lower() in ignored
                or text.isdigit()
            ):
                continue
            candidates.append(text)
        return max(candidates, key=len, default="")
