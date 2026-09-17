"""Version-aware batch processing and deterministic private alert drafts."""

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .agents import RelationshipAgent, TaxIntelligenceAgent
from .errors import error_details
from .models import ClientProfile, ImpactOutput

VERSION = "phase3-v1"
REFERENCE = re.compile(r"(?<!\d)\d{1,5}\s*/\s*20\d{2}(?!\d)")


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode()
    ).hexdigest()


def identity(doc: dict) -> str:
    return f"{doc['source']}:{doc['document_hash']}"


def snapshot(doc: dict) -> dict:
    content = doc.get("content") or ""
    return {
        "document_id": identity(doc),
        "source": doc["source"],
        "document_hash": doc["document_hash"],
        "content_hash": doc.get("content_hash"),
        "text_hash": digest(content),
        "title": doc["title"],
        "url": doc.get("detail_url"),
        "text": content[:18000],
        "truncated": len(content) > 18000,
    }


class EvidenceQuoteError(ValueError):
    """A specialist cited text absent from the supplied document version."""


def source_quote(text, quote):
    """Resolve whitespace-only differences and retain the exact source excerpt."""
    if quote and quote in text:
        return quote
    parts = quote.split()
    if not parts:
        return None
    match = re.search(r"\s+".join(re.escape(part) for part in parts), text)
    return match.group(0) if match else None


def validate_findings(primary_id, documents, relationships, impact=None):
    """Quote checks establish provenance only, not entailment or legal validity."""
    evidence_by_id = {d["document_id"]: d for d in documents}
    findings = list(relationships.relationships) + ([impact] if impact else [])
    for edge in relationships.relationships:
        if (
            edge.target_document_id not in evidence_by_id
            or edge.target_document_id == primary_id
        ):
            raise ValueError("Unresolved or self-referencing target")
        if edge.scope == "partial" and not edge.affected_provisions:
            raise ValueError("Partial relationship requires affected provisions")
    for finding in findings:
        if not any(e.document_id == primary_id for e in finding.evidence):
            raise ValueError("Finding lacks primary-publication evidence")
        for evidence in finding.evidence:
            doc = evidence_by_id.get(evidence.document_id)
            quote = source_quote(doc["text"], evidence.quote) if doc else None
            if quote is None:
                raise EvidenceQuoteError("Evidence quote not found in supplied version")
            evidence.quote = quote


def match_client(impact: ImpactOutput, client: ClientProfile) -> tuple[str, list[str]]:
    if not impact.applicability_complete or not impact.applicability_rules:
        return "needs_review", ["Applicability cannot be fully evaluated by MVP rules"]
    reasons = []
    missing = False
    for rule in impact.applicability_rules:
        value = getattr(client, rule.field)
        if value is None:
            missing = True
            reasons.append(f"Missing {rule.field}")
        elif value.strip().casefold() not in {
            v.strip().casefold() for v in rule.values
        }:
            return "not_matched", [f"{rule.field} does not match"]
        else:
            reasons.append(f"{rule.field} matches")
    return ("needs_review" if missing else "matched"), reasons


class IntelligenceService:
    def __init__(
        self, database, model_client=None, model="gpt-4.1", sensitive_values=()
    ):
        self.db = database
        self.client = model_client
        self.model = model
        self.sensitive_values = tuple(sensitive_values)

    def init_indexes(self):
        self.db.processing_runs.create_index([("process_type", 1), ("status", 1)])
        self.db.document_relationships.create_index("analysis_id")
        self.db.regulatory_impacts.create_index(
            [("document_id", 1), ("created_at", -1)]
        )
        self.db.client_profiles.create_index(
            [("tenant_id", 1), ("client_id", 1)], unique=True
        )
        self.db.client_alerts.create_index([("tenant_id", 1), ("client_id", 1)])

    def candidates(self, primary):
        # Exact numbered references, never a silent "latest N" substitute.
        refs = sorted(set(REFERENCE.findall((primary.get("content") or "")[:120000])))
        queries = [
            {
                "title": {
                    "$regex": r"(?<!\d)"
                    + r"\s*/\s*".join(
                        re.escape(part.strip()) for part in ref.split("/")
                    )
                    + r"(?!\d)",
                    "$options": "i",
                }
            }
            for ref in refs[:20]
        ]
        if not queries:
            return []
        return list(
            self.db.regulatory_documents.find(
                {
                    "source": primary["source"],
                    "document_hash": {"$ne": primary["document_hash"]},
                    "$or": queries,
                },
                {"_id": 0},
            )
            .sort("document_hash", 1)
            .limit(6)
        )

    def is_current(self, record):
        for version in record["input_versions"]:
            doc = self.db.regulatory_documents.find_one(
                {"source": version["source"], "document_hash": version["document_hash"]}
            )
            if doc is None or digest(doc.get("content") or "") != version["text_hash"]:
                return False
            if doc.get("content_hash") != version["content_hash"]:
                return False
        return True

    def analyze(self, source, document_hash):
        primary = self.db.regulatory_documents.find_one(
            {"source": source, "document_hash": document_hash}
        )
        if not primary or not (primary.get("content") or "").strip():
            raise ValueError("Stored document with extracted content required")
        documents = [snapshot(d) for d in [primary, *self.candidates(primary)]]
        versions = [{k: v for k, v in d.items() if k != "text"} for d in documents]
        run_id = digest([VERSION, self.model, versions])
        now = datetime.now(UTC)
        owner = uuid4().hex
        # _id prevents concurrent inserts; lease allows crash recovery.
        try:
            claim = self.db.processing_runs.find_one_and_update(
                {
                    "_id": run_id,
                    "$or": [
                        {"status": "failed"},
                        {"status": "running", "lease_until": {"$lt": now}},
                    ],
                },
                {
                    "$set": {
                        "process_type": VERSION,
                        "document_id": identity(primary),
                        "model": self.model,
                        "input_hash": run_id,
                        "input_versions": versions,
                        "status": "running",
                        "owner": owner,
                        "started_at": now,
                        "lease_until": now + timedelta(minutes=10),
                    },
                    "$inc": {"attempts": 1},
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            return {"analysis_id": run_id, "status": "already_claimed_or_completed"}
        try:
            primary_id = identity(primary)
            relationship_agent = RelationshipAgent(self.client)
            for attempt in range(2):
                relations = relationship_agent.analyze(
                    primary_id, documents, retry_evidence=bool(attempt)
                )
                try:
                    validate_findings(primary_id, documents, relations)
                    break
                except EvidenceQuoteError:
                    if attempt:
                        raise
            impact_agent = TaxIntelligenceAgent(self.client)
            for attempt in range(2):
                impact = impact_agent.analyze(
                    primary_id, documents, relations, retry_evidence=bool(attempt)
                )
                try:
                    validate_findings(primary_id, documents, relations, impact)
                    break
                except EvidenceQuoteError:
                    if attempt:
                        raise
            if (
                any(d["truncated"] for d in documents)
                or relations.unresolved_references
            ):
                impact.applicability_complete = False
                impact.uncertainties.append(
                    "Incomplete source text or unresolved references require CA review."
                )
            if not self.is_current(claim):
                raise ValueError("Source changed during analysis")
            if not self.db.processing_runs.find_one(
                {
                    "_id": run_id,
                    "owner": owner,
                    "lease_until": {"$gt": datetime.now(UTC)},
                }
            ):
                raise ValueError("Analysis lease expired")
            base = {
                "analysis_id": run_id,
                "document_id": identity(primary),
                "input_versions": versions,
                "review_status": "pending_review",
                "created_at": now,
            }
            for index, edge in enumerate(relations.relationships):
                self.db.document_relationships.replace_one(
                    {"_id": f"{run_id}:{index}"},
                    {
                        "_id": f"{run_id}:{index}",
                        **base,
                        **edge.model_dump(mode="json"),
                        "source": {"type": "document", "id": identity(primary)},
                        "target": {"type": "document", "id": edge.target_document_id},
                        "verification_status": "unverified",
                    },
                    upsert=True,
                )
            self.db.regulatory_impacts.replace_one(
                {"_id": run_id},
                {
                    "_id": run_id,
                    **base,
                    "impact": impact.model_dump(mode="json"),
                    "relationships": relations.model_dump(mode="json"),
                },
                upsert=True,
            )
            result = self.db.processing_runs.update_one(
                {"_id": run_id, "owner": owner, "status": "running"},
                {
                    "$set": {"status": "succeeded", "completed_at": datetime.now(UTC)},
                    "$unset": {
                        "error_type": "",
                        "error_message": "",
                        "error_trace": "",
                    },
                },
            )
            if result.modified_count != 1:
                raise ValueError("Analysis ownership lost")
            return {"analysis_id": run_id, "status": "pending_review"}
        except Exception as exc:
            self.db.processing_runs.update_one(
                {"_id": run_id, "owner": owner},
                {
                    "$set": {
                        "status": "failed",
                        **error_details(exc, self.sensitive_values),
                        "completed_at": datetime.now(UTC),
                    }
                },
            )
            raise

    def _records(self, collection, query, projection):
        """Keyset pages avoid keeping a cursor idle during slow model calls."""
        last_id = None
        while True:
            filters = dict(query)
            if last_id is not None:
                filters["_id"] = {"$gt": last_id}
            with collection.find(filters, projection).sort("_id", 1).limit(
                100
            ) as cursor:
                page = list(cursor)
            if not page:
                return
            yield from page
            last_id = page[-1]["_id"]

    def analyze_source(self, source):
        summary = {
            "source": source,
            "total": 0,
            "completed": 0,
            "skipped": 0,
            "failed": 0,
            "completed_analyses": [],
            "skipped_analyses": [],
            "failures": [],
        }
        for record in self._records(
            self.db.regulatory_documents, {"source": source}, {"document_hash": 1}
        ):
            summary["total"] += 1
            document_hash = record.get("document_hash")
            try:
                if not document_hash:
                    raise ValueError("Document hash missing")
                result = self.analyze(source, document_hash)
                item = {"document_hash": document_hash, **result}
                if result["status"] == "already_claimed_or_completed":
                    summary["skipped"] += 1
                    summary["skipped_analyses"].append(item)
                else:
                    summary["completed"] += 1
                    summary["completed_analyses"].append(item)
            except Exception as exc:  # noqa: BLE001 -- isolate each batch record
                summary["failed"] += 1
                summary["failures"].append(
                    {
                        "document_hash": document_hash,
                        "record_id": str(record["_id"]),
                        "error_type": type(exc).__name__,
                    }
                )
        return summary

    def usable(self, record):
        return bool(
            record
            and self.db.processing_runs.find_one(
                {"_id": record["analysis_id"], "status": "succeeded"}
            )
            and self.is_current(record)
        )

    def get_intelligence(self, source, document_hash):
        for record in (
            self.db.regulatory_impacts.find(
                {"document_id": f"{source}:{document_hash}"}
            )
            .sort("created_at", -1)
            .limit(20)
        ):
            if self.usable(record):
                record["id"] = record.pop("_id")
                return record
        return None

    def review(self, analysis_id, reviewer, decision):
        if decision not in ("approved", "rejected") or not reviewer.strip():
            raise ValueError("Reviewer and approved/rejected decision required")
        record = self.db.regulatory_impacts.find_one({"_id": analysis_id})
        if not self.usable(record):
            raise ValueError("Analysis missing, incomplete or stale")
        audit = {"reviewer": reviewer, "decision": decision, "at": datetime.now(UTC)}

        def persist(session):
            # One transaction prevents approved impacts with pending edges (or vice versa).
            result = self.db.regulatory_impacts.update_one(
                {"_id": analysis_id},
                {"$set": {"review_status": decision}, "$push": {"reviews": audit}},
                session=session,
            )
            if result.matched_count != 1:
                raise ValueError("Analysis disappeared during review")
            self.db.document_relationships.update_many(
                {"analysis_id": analysis_id},
                {
                    "$set": {
                        "review_status": decision,
                        "verification_status": (
                            "verified" if decision == "approved" else "rejected"
                        ),
                    },
                    "$push": {"reviews": audit},
                },
                session=session,
            )

        with self.db.client.start_session() as session:
            session.with_transaction(persist)

    def review_source(self, source, reviewer, decision):
        if decision not in ("approved", "rejected") or not reviewer.strip():
            raise ValueError("Reviewer and approved/rejected decision required")
        summary = {
            "source": source,
            "decision": decision,
            "total": 0,
            "completed": 0,
            "failed": 0,
            "completed_analyses": [],
            "failures": [],
        }
        # Include same-decision records to repair edges reviewed by older code.
        # An opposite explicit decision is never silently overwritten by bulk review.
        query = {
            "document_id": {"$regex": "^" + re.escape(source) + ":"},
            "review_status": {"$in": ["pending_review", decision]},
        }
        for record in self._records(self.db.regulatory_impacts, query, {"_id": 1}):
            summary["total"] += 1
            analysis_id = record["_id"]
            try:
                self.review(analysis_id, reviewer, decision)
                summary["completed"] += 1
                summary["completed_analyses"].append(analysis_id)
            except Exception as exc:  # noqa: BLE001 -- isolate each batch record
                summary["failed"] += 1
                summary["failures"].append(
                    {"analysis_id": analysis_id, "error_type": type(exc).__name__}
                )
        return summary

    def save_client(self, profile: ClientProfile):
        self.db.client_profiles.replace_one(
            {"tenant_id": profile.tenant_id, "client_id": profile.client_id},
            profile.model_dump(),
            upsert=True,
        )

    def build_alerts(self, tenant_id, analysis_id):
        record = self.db.regulatory_impacts.find_one({"_id": analysis_id})
        if not self.usable(record) or record["review_status"] != "approved":
            raise ValueError("Current, CA-approved impact required")
        impact = ImpactOutput.model_validate(record["impact"])
        count = 0
        for data in self.db.client_profiles.find({"tenant_id": tenant_id}, {"_id": 0}):
            profile = ClientProfile.model_validate(data)
            match, reasons = match_client(impact, profile)
            key = digest([VERSION, tenant_id, profile.client_id, analysis_id])
            # Includes not_matched results to replace obsolete draft decisions.
            self.db.client_alerts.replace_one(
                {"_id": key},
                {
                    "_id": key,
                    "tenant_id": tenant_id,
                    "client_id": profile.client_id,
                    "analysis_id": analysis_id,
                    "profile_hash": digest(data),
                    "match": match,
                    "reasons": reasons,
                    "status": "draft",
                    "summary": impact.summary,
                    "ca_actions": impact.ca_actions,
                    "evidence": [e.model_dump() for e in impact.evidence],
                    "created_at": datetime.now(UTC),
                },
                upsert=True,
            )
            count += 1
        return count

    def alerts(self, tenant_id):
        results = []
        for alert in (
            self.db.client_alerts.find(
                {"tenant_id": tenant_id, "match": {"$ne": "not_matched"}}
            )
            .sort("created_at", -1)
            .limit(100)
        ):
            profile = self.db.client_profiles.find_one(
                {"tenant_id": tenant_id, "client_id": alert["client_id"]}, {"_id": 0}
            )
            impact = self.db.regulatory_impacts.find_one({"_id": alert["analysis_id"]})
            if (
                profile
                and digest(profile) == alert["profile_hash"]
                and self.usable(impact)
                and impact["review_status"] == "approved"
            ):
                alert["id"] = alert.pop("_id")
                results.append(alert)
        return results
