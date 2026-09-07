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
            url="https://example.test/rbi", document_type="notification"
        )
        soup = self.soup(html)
        results: list[BaseRegulatoryDocument] = []
        current_date_text: str | None = None
        for row in soup.select("tr"):
            date_header = row.select_one("td.tableheader")
            if date_header is not None:
                current_date_text = date_header.get_text(" ", strip=True)
                continue

            for link in row.select('a[href*="NotificationUser.aspx?Id="]'):
                title = link.get_text(" ", strip=True)
                if not title:
                    continue

                date_text = current_date_text
                if date_text is None:
                    first_cell = row.find("td")
                    if first_cell is not None and link not in first_cell.descendants:
                        date_text = first_cell.get_text(" ", strip=True)

                results.append(
                    self.candidate(
                        title=title,
                        href=link.get("href", ""),
                        date_text=date_text,
                        document_type=page.document_type,
                        base_url=str(page.url),
                        transport=page.transport,
                    )
                )
                if len(results) >= limit:
                    return results
        return results
