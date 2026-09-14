"""Read queries over the ingestion-owned collections; never mutate their schema."""

import re
from datetime import UTC, datetime
from typing import Any

from pymongo.database import Database

from .models import Authority, AuthorityOverview, Document, DocumentPage, Overview


def document_filter(
    source: Authority | None, query: str, year: int | None
) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    if source is not None:
        filters["source"] = source.value
    if query.strip():
        filters["title"] = {"$regex": re.escape(query.strip()), "$options": "i"}
    if year is not None:
        filters["published_date"] = {
            "$gte": datetime(year, 1, 1, tzinfo=UTC),
            "$lt": datetime(year + 1, 1, 1, tzinfo=UTC),
        }
    return filters


class DashboardRepository:
    def __init__(self, database: Database[dict[str, Any]]) -> None:
        self.database = database

    def documents(
        self,
        source: Authority | None = None,
        query: str = "",
        year: int | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> DocumentPage:
        filters = document_filter(source, query, year)
        total = self.database.regulatory_documents.count_documents(filters)
        # Slice content in MongoDB so listings do not transfer entire PDFs.
        records = self.database.regulatory_documents.aggregate(
            [
                {"$match": filters},
                {"$sort": {"published_date": -1, "_id": -1}},
                {"$skip": (page - 1) * page_size},
                {"$limit": page_size},
                {
                    "$project": {
                        "_id": 0,
                        "source": 1,
                        "document_hash": 1,
                        "title": 1,
                        "published_date": 1,
                        "document_type": 1,
                        "detail_url": 1,
                        "attachment_url": 1,
                        "excerpt": {
                            "$substrCP": [{"$ifNull": ["$content", ""]}, 0, 320]
                        },
                    }
                },
            ]
        )
        return DocumentPage(
            items=[Document.model_validate(record) for record in records],
            total=total,
            page=page,
            page_size=page_size,
        )

    def overview(self, year: int | None = None) -> Overview:
        authorities = []
        for source in Authority:
            documents = self.documents(source=source, year=year, page_size=3)
            latest = documents.items[0] if documents.items else None
            run = self.database.ingestion_runs.find_one(
                {"source": source.value}, sort=[("recorded_at", -1), ("_id", -1)]
            )
            authorities.append(
                AuthorityOverview(
                    source=source,
                    total=documents.total,
                    latest_year=(
                        latest.published_date.year
                        if latest and latest.published_date
                        else None
                    ),
                    last_checked_at=run.get("recorded_at") if run else None,
                    last_run_status=(
                        ("failed" if run.get("failed", 0) else "succeeded")
                        if run
                        else None
                    ),
                    preview=documents.items,
                )
            )
        return Overview(
            total=sum(item.total for item in authorities), authorities=authorities
        )

    def detail(self, source: Authority, document_hash: str) -> dict[str, Any] | None:
        return self.database.regulatory_documents.find_one(
            {"source": source.value, "document_hash": document_hash}, {"_id": 0}
        )

    def publication_years(self, source: Authority | None = None) -> list[int]:
        filters: dict[str, Any] = {"published_date": {"$type": "date"}}
        if source is not None:
            filters["source"] = source.value
        records = self.database.regulatory_documents.aggregate(
            [
                {"$match": filters},
                {"$group": {"_id": {"$year": "$published_date"}}},
                {"$match": {"_id": {"$gte": 1900, "$lte": 9998}}},
                {"$sort": {"_id": -1}},
            ],
            maxTimeMS=5000,
        )
        return [record["_id"] for record in records]
