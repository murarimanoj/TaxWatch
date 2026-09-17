"""Read-only graph expansion. Approved edges are hints, not primary evidence."""

from datetime import date

from ..chat import Citation
from ..models import Authority
from ..regulatory_intelligence.service import digest, source_quote


class RelationshipLookup:
    def __init__(self, database):
        self.database = database

    def expand(
        self,
        seeds: list[Citation],
        source: Authority | None,
        year: int | None,
        as_of: date,
    ) -> tuple[list[dict], list[dict]]:
        identities = sorted({f"{s.source.value}:{s.document_hash}" for s in seeds})
        if not identities:
            return [], []
        records, hints = [], []
        cache = {}

        def document(identifier):
            if identifier in cache:
                return cache[identifier]
            if (
                len(cache) >= 16
                or not isinstance(identifier, str)
                or ":" not in identifier
            ):
                return None
            authority, document_hash = identifier.split(":", 1)
            if authority not in {s.value for s in Authority} or (
                source and authority != source.value
            ):
                return None
            doc = self.database.regulatory_documents.find_one(
                {"source": authority, "document_hash": document_hash},
                {
                    "_id": 0,
                    "source": 1,
                    "document_hash": 1,
                    "content": 1,
                    "content_hash": 1,
                    "title": 1,
                    "detail_url": 1,
                    "published_date": 1,
                },
            )
            cache[identifier] = doc
            if doc and year is not None:
                published = doc.get("published_date")
                if not published or str(published)[:4] != str(year):
                    cache[identifier] = None
            return cache[identifier]

        query = {
            "review_status": "approved",
            "verification_status": "verified",
            "$or": [
                {"source.id": {"$in": identities}},
                {"target.id": {"$in": identities}},
            ],
        }
        for edge in (
            self.database.document_relationships.find(query).sort("_id", 1).limit(12)
        ):
            # Defense in depth if a repository adapter returns unexpected records.
            if (
                edge.get("review_status") != "approved"
                or edge.get("verification_status") != "verified"
            ):
                continue
            origin = edge.get("source", {}).get("id")
            target = edge.get("target", {}).get("id")
            if origin not in identities and target not in identities:
                continue
            versions = edge.get("input_versions")
            if not versions or len(versions) > 7:
                continue
            version_ids = {v.get("document_id") for v in versions}
            if not {origin, target}.issubset(version_ids):
                continue
            run_id = edge.get("analysis_id")
            if not run_id or not self.database.processing_runs.find_one(
                {"_id": run_id, "status": "succeeded"}, {"_id": 1}
            ):
                continue
            current = True
            for version in versions:
                doc = document(version.get("document_id"))
                if (
                    not doc
                    or digest(doc.get("content") or "") != version.get("text_hash")
                    or doc.get("content_hash") != version.get("content_hash")
                ):
                    current = False
                    break
            if not current:
                continue
            excerpts = []
            for evidence in edge.get("evidence", [])[:6]:
                doc = document(evidence.get("document_id"))
                quote = (
                    source_quote(doc.get("content") or "", evidence.get("quote", ""))
                    if doc
                    else None
                )
                if quote:
                    excerpts.append((doc, quote[:1800]))
            if not excerpts:
                continue
            for identifier in (origin, target):
                doc = document(identifier)
                if doc and not any(
                    d["document_hash"] == doc["document_hash"]
                    and d["source"] == doc["source"]
                    for d, _ in excerpts
                ):
                    excerpts.append((doc, (doc.get("content") or "")[:1800]))
            for doc, passage in excerpts:
                if passage.strip():
                    records.append(
                        {
                            "source": doc["source"],
                            "document_hash": doc["document_hash"],
                            "title": doc["title"],
                            "url": doc["detail_url"],
                            "published_date": (
                                str(doc["published_date"])
                                if doc.get("published_date")
                                else None
                            ),
                            "passage": passage,
                        }
                    )
            effective = edge.get("effective_from")
            try:
                effective_date = (
                    date.fromisoformat(str(effective)[:10]) if effective else None
                )
            except ValueError:
                effective_date = None
            hints.append(
                {
                    "source_document_id": origin,
                    "target_document_id": target,
                    "relationship_type": edge.get("relationship_type"),
                    "scope": edge.get("scope", "unknown"),
                    "affected_provisions": edge.get("affected_provisions", [])[:20],
                    "effective_from": (
                        effective_date.isoformat() if effective_date else None
                    ),
                    "temporal_context": (
                        "unknown"
                        if not effective_date
                        else (
                            "future" if effective_date > as_of else "on_or_before_as_of"
                        )
                    ),
                    "review_status": "approved",
                }
            )
        return records[:24], hints
