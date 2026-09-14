from datetime import UTC, date, datetime, time
from enum import Enum
from typing import Any

from pydantic import AnyUrl
from pymongo import ASCENDING, MongoClient, ReplaceOne
from pymongo.operations import SearchIndexModel

from .config import SourceCatalog
from .domain import (
    DocumentChunk,
    RegulatoryDocument,
    RegulatorySource,
    RunSummary,
    Source,
)
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
            category_counts[page.document_type] = (
                category_counts.get(page.document_type, 0) + 1
            )
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
        self._chunks = self._db.document_chunks
        self._artifacts = self._db.document_artifacts
        self._provisions = self._db.regulatory_provisions
        self._relationships = self._db.document_relationships
        self._jobs = self._db.ingestion_jobs
        self._processing_runs = self._db.processing_runs

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
        self._sources.create_index(
            [("source_id", ASCENDING)], unique=True, name="source_id_unique"
        )
        self._sources.create_index([("authority", ASCENDING), ("enabled", ASCENDING)])
        self._runs.create_index(
            [("source", ASCENDING), ("recorded_at", ASCENDING)],
            name="source_recorded_at",
        )
        self._runs.create_index([("recorded_at", ASCENDING)], name="recorded_at")
        self.ensure_chunk_indexes()
        self._artifacts.create_index(
            [("document_id", ASCENDING), ("file_hash", ASCENDING)],
            unique=True,
            name="document_file_unique",
        )
        self._provisions.create_index(
            [("provision_id", ASCENDING)], unique=True, name="provision_id_unique"
        )
        self._relationships.create_index(
            [("source.id", ASCENDING), ("relationship_type", ASCENDING)]
        )
        self._relationships.create_index(
            [("target.id", ASCENDING), ("relationship_type", ASCENDING)]
        )
        self._jobs.create_index([("source_id", ASCENDING), ("started_at", ASCENDING)])
        self._jobs.create_index([("status", ASCENDING), ("current_stage", ASCENDING)])
        self._processing_runs.create_index(
            [
                ("document_id", ASCENDING),
                ("process_type", ASCENDING),
                ("started_at", ASCENDING),
            ]
        )

    def ensure_chunk_indexes(self) -> None:
        self._chunks.create_index(
            [("chunk_id", ASCENDING)], unique=True, name="chunk_id_unique"
        )
        self._chunks.create_index(
            [
                ("source", ASCENDING),
                ("document_hash", ASCENDING),
                ("chunk_index", ASCENDING),
            ],
            unique=True,
            name="source_document_chunk_unique",
        )
        self._chunks.create_index(
            [("document_hash", ASCENDING), ("embedding_model", ASCENDING)]
        )
        self._chunks.create_index([("text", "text")], name="chunk_text")

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

    def ensure_vector_index(self, dimensions: int) -> None:
        """Create the Atlas Vector Search index separately from ordinary BSON indexes."""
        name = "document_chunks_vector"
        if any(
            index.get("name") == name for index in self._chunks.list_search_indexes()
        ):
            return
        definition = {
            "fields": [
                {
                    "type": "vector",
                    "path": "embedding",
                    "numDimensions": dimensions,
                    "similarity": "cosine",
                },
                {"type": "filter", "path": "source"},
                {"type": "filter", "path": "document_type"},
                {"type": "filter", "path": "published_date"},
                {"type": "filter", "path": "document_hash"},
            ]
        }
        self._chunks.create_search_index(
            SearchIndexModel(definition=definition, name=name, type="vectorSearch")
        )

    def upsert(self, document: RegulatoryDocument) -> str:
        payload = to_bson(document.model_dump(mode="python"))
        result = self._documents.update_one(
            {
                "source": document.source.value,
                "document_hash": document.document_hash,
            },
            {
                "$set": {
                    **payload,
                    "source": document.source.value,
                    "updated_at": datetime.now(UTC),
                },
                "$setOnInsert": {"created_at": datetime.now(UTC)},
            },
            upsert=True,
        )
        if result.upserted_id is not None:
            return "inserted"
        return "updated" if result.modified_count else "unchanged"

    def iter_documents(
        self, source: Source | None, limit: int
    ) -> list[RegulatoryDocument]:
        query = {"source": source.value} if source is not None else {}
        cursor = (
            self._documents.find(query, {"_id": 0})
            .sort([("published_date", -1), ("document_hash", 1)])
            .limit(limit)
        )
        return [RegulatoryDocument.model_validate(record) for record in cursor]

    def record_run(self, summary: RunSummary) -> None:
        now = datetime.now(UTC)
        self._runs.insert_one({**summary.model_dump(mode="python"), "recorded_at": now})
        self._sources.update_many(
            {"authority": summary.source.value, "enabled": True},
            {
                "$set": {
                    "last_checked_at": now,
                    "last_run_status": "failed" if summary.failed else "succeeded",
                }
            },
        )

    def chunks_current(
        self, document: RegulatoryDocument, model: str, expected_count: int
    ) -> bool:
        current_count = self._chunks.count_documents(
            {
                "source": document.source.value,
                "document_hash": document.document_hash,
                "content_hash": document.content_hash,
                "embedding_model": model,
            }
        )
        total_count = self._chunks.count_documents(
            {"source": document.source.value, "document_hash": document.document_hash}
        )
        return current_count == expected_count and total_count == expected_count

    def replace_chunks(
        self, document: RegulatoryDocument, chunks: list[DocumentChunk]
    ) -> None:
        now = datetime.now(UTC)
        self._chunks.bulk_write(
            [
                ReplaceOne(
                    {
                        "source": document.source.value,
                        "document_hash": document.document_hash,
                        "chunk_index": chunk.chunk_index,
                    },
                    {**to_bson(chunk.model_dump(mode="python")), "updated_at": now},
                    upsert=True,
                )
                for chunk in chunks
            ],
            ordered=True,
        )
        self._chunks.delete_many(
            {
                "source": document.source.value,
                "document_hash": document.document_hash,
                "chunk_index": {"$gte": len(chunks)},
            }
        )


class NullDocumentRepository:
    def ensure_indexes(self) -> None:
        pass

    def upsert(self, document: RegulatoryDocument) -> str:
        return "unchanged"

    def iter_documents(
        self, source: Source | None, limit: int
    ) -> list[RegulatoryDocument]:
        return []

    def sync_sources(self, sources: list[RegulatorySource]) -> None:
        pass

    def record_run(self, summary: RunSummary) -> None:
        pass

    def chunks_current(
        self, document: RegulatoryDocument, model: str, expected_count: int
    ) -> bool:
        return False

    def replace_chunks(
        self, document: RegulatoryDocument, chunks: list[DocumentChunk]
    ) -> None:
        pass
