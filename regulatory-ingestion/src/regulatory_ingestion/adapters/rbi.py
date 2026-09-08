from datetime import date
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

from ..config import SourcePageConfig
from ..domain import BaseRegulatoryDocument, Source
from .base import HtmlListingAdapter


class RbiAdapter(HtmlListingAdapter):
    source = Source.RBI

    def parse_listing(
        self,
        html: str,
        limit: int,
        page: SourcePageConfig | None = None,
    ) -> list[BaseRegulatoryDocument]:
        page = page or SourcePageConfig(
            url="https://example.test/rbi.xml",
            document_type="notification",
        )
        try:
            root = ElementTree.fromstring(html.lstrip("\ufeff"))
        except ElementTree.ParseError as exc:
            raise ValueError("RBI RSS response is not valid XML") from exc

        results: list[BaseRegulatoryDocument] = []
        for item in root.findall("./channel/item"):
            title = self._element_text(item, "title")
            link = self._element_text(item, "link")
            published_date = self._published_date(
                self._element_text(item, "pubDate")
            )
            if not title or not link:
                continue

            normalized_title = " ".join(title.split())
            results.append(
                BaseRegulatoryDocument(
                    source=self.source,
                    title=normalized_title,
                    published_date=published_date,
                    document_type=page.document_type,
                    detail_url=link,
                    metadata={"transport": page.transport},
                )
            )
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def _element_text(item: ElementTree.Element, name: str) -> str | None:
        element = item.find(name)
        if element is None or element.text is None:
            return None
        value = element.text.strip()
        return value or None

    @staticmethod
    def _published_date(value: str | None) -> date | None:
        if value is None:
            return None
        try:
            return parsedate_to_datetime(value).date()
        except (TypeError, ValueError, OverflowError):
            return None
