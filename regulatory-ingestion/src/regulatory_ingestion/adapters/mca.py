import json
from base64 import b64encode
from datetime import date
from time import strptime
from typing import Any
from urllib.parse import urlencode

from ..config import SourcePageConfig
from ..domain import BaseRegulatoryDocument, Source
from .base import HtmlListingAdapter


class McaAdapter(HtmlListingAdapter):
    """Discover circular PDFs starting from the configured MCA page."""

    source = Source.MCA
    _metadata_path = (
        "/bin/ebook/service/documentMetadata"
        "?docCategory=Circulars&flag=initial&status=Current"
    )
    _download_url = "https://www.mca.gov.in/bin/ebook/dms/getdocument"

    def discover(self, limit: int) -> list[BaseRegulatoryDocument]:
        if self.browser is None:
            raise RuntimeError("MCA discovery requires Playwright")

        results: list[BaseRegulatoryDocument] = []
        for page in self.pages:
            response = self.browser.get_page_resource(
                str(page.url),
                self._metadata_path,
            )
            results.extend(self.parse_listing(response.text, limit, page))
        return results

    def parse_listing(
        self,
        html: str,
        limit: int,
        page: SourcePageConfig | None = None,
    ) -> list[BaseRegulatoryDocument]:
        try:
            payload: Any = json.loads(html)
        except json.JSONDecodeError as exc:
            raise ValueError("MCA metadata response is not valid JSON") from exc

        records = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(records, list):
            raise TypeError("MCA metadata response does not contain a data list")

        parsed_records: list[tuple[date, dict[str, Any]]] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            published_date = self._parse_date(record.get("notificationdate"))
            if published_date is not None:
                parsed_records.append((published_date, record))
        parsed_records.sort(key=lambda item: item[0], reverse=True)

        document_type = page.document_type if page else "circular"
        transport = page.transport if page else "playwright"
        results: list[BaseRegulatoryDocument] = []
        for published_date, record in parsed_records:
            document_id = str(record.get("link", "")).strip()
            title = " ".join(str(record.get("shortDescription", "")).split())
            title = title.rstrip(" |").strip()
            if not document_id or not title:
                continue

            document_url = self._document_url(document_id)
            results.append(
                BaseRegulatoryDocument(
                    source=self.source,
                    title=title,
                    published_date=published_date,
                    document_type=document_type,
                    detail_url=document_url,
                    attachment_url=document_url,
                    metadata={
                        "transport": transport,
                        "referer": str(page.url) if page else "",
                    },
                )
            )
            if len(results) >= limit:
                break
        return results

    @classmethod
    def _document_url(cls, document_id: str) -> str:
        encoded_id = b64encode(document_id.encode()).decode("ascii")
        query = urlencode(
            {
                "doc": encoded_id,
                "docCategory": "Circulars",
                "actionType": "download",
            }
        )
        return f"{cls._download_url}?{query}"

    @staticmethod
    def _parse_date(value: object) -> date | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = strptime(value.strip(), "%m/%d/%Y")
        except ValueError:
            return None
        return date(parsed.tm_year, parsed.tm_mon, parsed.tm_mday)
