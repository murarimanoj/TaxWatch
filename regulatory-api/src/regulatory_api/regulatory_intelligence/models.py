"""Strict proposal contracts. Provenance validation is not legal approval."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(StrictModel):
    document_id: str
    quote: str = Field(min_length=15, max_length=1500)


class Relationship(StrictModel):
    target_document_id: str
    relationship_type: Literal[
        "REFERS_TO", "AMENDS", "CLARIFIES", "SUPERSEDES", "RESCINDS"
    ]
    scope: Literal["full", "partial", "unknown"]
    affected_provisions: list[str] = Field(max_length=20)
    effective_from: date | None
    explanation: str = Field(min_length=1, max_length=1500)
    evidence: list[Evidence] = Field(min_length=1, max_length=6)


class RelationshipOutput(StrictModel):
    relationships: list[Relationship] = Field(max_length=12)
    unresolved_references: list[str] = Field(max_length=20)
    uncertainties: list[str] = Field(max_length=20)


class Rule(StrictModel):
    """MVP categorical predicates only; complex thresholds require human review."""

    field: Literal["entity_type", "industry", "tax_registration", "jurisdiction"]
    values: list[str] = Field(min_length=1, max_length=20)


class ImpactOutput(StrictModel):
    summary: str = Field(min_length=1, max_length=2500)
    what_changed: list[str] = Field(max_length=15)
    affected_provisions: list[str] = Field(max_length=20)
    ca_actions: list[str] = Field(max_length=15)
    effective_from: date | None
    deadlines: list[str] = Field(max_length=10)
    urgency: Literal["low", "medium", "high", "unknown"]
    # All rules must match. Empty means unknown, never "everyone".
    applicability_rules: list[Rule] = Field(max_length=10)
    applicability_complete: bool
    uncertainties: list[str] = Field(max_length=20)
    evidence: list[Evidence] = Field(min_length=1, max_length=8)


class ClientProfile(StrictModel):
    tenant_id: str = Field(min_length=1, max_length=100)
    client_id: str = Field(min_length=1, max_length=100)
    entity_type: str | None = None
    industry: str | None = None
    tax_registration: str | None = None
    jurisdiction: str | None = None
