from datetime import UTC, date, datetime, time
from enum import Enum
from typing import Any

from pydantic import AnyUrl
from pymongo import ASCENDING, MongoClient

from .config import SourceCatalog
from .domain import RegulatoryDocument, RegulatorySource, RunSummary, Source
from .hashing import document_hash


def to_bson(value: Any) -> Any:
    """Convert Pydantic/domain values to types accepted by MongoDB's BSON encoder."""
    if isinstance(value, AnyUrl):
        return str(value)
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=UTC)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: to_bson(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_bson(item) for item in value]
    return value


def configured_sources(catalog: SourceCatalog) -> list[RegulatorySource]:
    records: list[RegulatorySource] = []
    for authority_name, source_config in catalog.sources.items():
        authority = Source(authority_name)
        category_counts: dict[str, int] = {}
        for page in source_config.pages:
            category_counts[page.document_type] = category_counts.get(page.document_type, 0) + 1
            ordinal = category_counts[page.document_type]
            suffix = "" if ordinal == 1 else f"_{ordinal}"
            records.append(
                RegulatorySource(
                    source_id=f"source_{authority.value}_{page.document_type}{suffix}",
                    authority=authority,
                    category=page.document_type,
                    adapter=source_config.adapter,
                    transport=page.transport,
                    discovery_url=page.url,
                )
            )
    return records


class MongoDocumentRepository:
    def __init__(self, uri: str, database: str) -> None:
        self._client = MongoClient(uri, serverSelectionTimeoutMS=10_000)
        self._db = self._client[database]
        self._documents = self._db.regulatory_documents
        self._runs = self._db.ingestion_runs
        self._sources = self._db.regulatory_sources

    def ensure_indexes(self) -> None:
        for record in self._documents.find(
            {"document_hash": {"$exists": False}},
            {"source": 1, "published_date": 1, "title": 1},
        ):
            identity_hash = document_hash(
                record["source"],
                record.get("published_date"),
                record["title"],
            )
            self._documents.update_one(
                {"_id": record["_id"]},
                {"$set": {"document_hash": identity_hash}},
            )

        self._documents.update_many(
            {"external_id": {"$exists": True}},
            {"$unset": {"external_id": ""}},
        )
        if "source_external_id_unique" in self._documents.index_information():
            self._documents.drop_index("source_external_id_unique")
        if "document_hash_unique" in self._documents.index_information():
            self._documents.drop_index("document_hash_unique")
        self._documents.create_index(
            [("source", ASCENDING), ("document_hash", ASCENDING)],
            unique=True,
            name="source_document_hash_unique",
        )
        self._documents.create_index([("published_date", ASCENDING)])
        self._sources.create_index([("source_id", ASCENDING)], unique=True, name="source_id_unique")
        self._sources.create_index([("authority", ASCENDING), ("enabled", ASCENDING)])
        self._runs.create_index(
            [("source", ASCENDING), ("recorded_at", ASCENDING)],
            name="source_recorded_at",
        )
        self._runs.create_index([("recorded_at", ASCENDING)], name="recorded_at")

    def sync_sources(self, sources: list[RegulatorySource]) -> None:
        now = datetime.now(UTC)
        active_ids = [source.source_id for source in sources]
        self._sources.update_many(
            {"source_id": {"$nin": active_ids}},
            {"$set": {"enabled": False, "updated_at": now}},
        )
        for source in sources:
            payload = source.model_dump(mode="python")
            payload["authority"] = source.authority.value
            payload["discovery_url"] = str(source.discovery_url)
            self._sources.update_one(
                {"source_id": source.source_id},
                {
                    "$set": {**payload, "updated_at": now},
                    "$setOnInsert": {"created_at": now},
                },
                upsert=True,
            )

    def upsert(self, document: RegulatoryDocument) -> str:
        payload = to_bson(document.model_dump(mode="python"))
        result = self._documents.update_one(
            {
                "source": document.source.value,
                "document_hash": document.document_hash,
            },
            {
                "$set": {**payload, "source": document.source.value, "updated_at": datetime.now(UTC)},
                "$setOnInsert": {"created_at": datetime.now(UTC)},
            },
            upsert=True,
        )
        if result.upserted_id is not None:
            return "inserted"
        return "updated" if result.modified_count else "unchanged"

    def record_run(self, summary: RunSummary) -> None:
        now = datetime.now(UTC)
        self._runs.insert_one({**summary.model_dump(mode="python"), "recorded_at": now})
        self._sources.update_many(
            {"authority": summary.source.value, "enabled": True},
            {"$set": {
                "last_checked_at": now,
                "last_run_status": "failed" if summary.failed else "succeeded",
            }},
        )


class NullDocumentRepository:
    def ensure_indexes(self) -> None:
        pass

    def upsert(self, document: RegulatoryDocument) -> str:
        return "unchanged"

    def sync_sources(self, sources: list[RegulatorySource]) -> None:
        pass

    def record_run(self, summary: RunSummary) -> None:
        pass
