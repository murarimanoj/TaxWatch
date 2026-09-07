import re

from ..config import SourcePageConfig
from ..domain import BaseRegulatoryDocument, Source
from .base import HtmlListingAdapter


class GstAdapter(HtmlListingAdapter):
    source = Source.GST

    def parse_listing(
        self,
        html: str,
        limit: int,
        page: SourcePageConfig | None = None,
    ) -> list[BaseRegulatoryDocument]:
        page = page or SourcePageConfig(
            url="https://example.test/gst", document_type="cgst_tax_notification"
        )
        soup = self.soup(html)
        results: list[BaseRegulatoryDocument] = []
        for row in soup.select("table.customdatatable tbody tr"):
            english_link = row.select_one(
                "td.views-field-field-tax-ntfcn-english a[href]"
            )
            if english_link is None:
                continue

            number_node = row.select_one(
                "td.views-field-field-notification-no-date-of-is"
            )
            subject_node = row.select_one("td.views-field-body")
            notification_number = (
                number_node.get_text(" ", strip=True) if number_node else ""
            )
            subject = subject_node.get_text(" ", strip=True) if subject_node else ""
            title = " - ".join(
                part for part in (notification_number, subject) if part
            )
            if not title:
                continue

            results.append(
                self.candidate(
                    title=title,
                    href=english_link.get("href", ""),
                    date_text=self._extract_date(notification_number),
                    document_type=page.document_type,
                    base_url=str(page.url),
                    transport=page.transport,
                )
            )
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def _extract_date(value: str) -> str | None:
        full_date_match = re.search(
            r"\b(?:\d{1,2}[./-]\d{1,2}[./-]\d{4}|"
            r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
            r"[a-z]*\s+\d{1,2},\s+\d{4})\b",
            value,
            flags=re.IGNORECASE,
        )
        if full_date_match is not None:
            return full_date_match.group(0)

        month_year_match = re.match(r"(0?[1-9]|1[0-2])/(\d{4})\b", value)
        if month_year_match is None:
            return None
        month, year = month_year_match.groups()
        return f"01.{int(month):02d}.{year}"
