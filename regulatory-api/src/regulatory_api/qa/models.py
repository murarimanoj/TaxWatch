from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RoutePlan(StrictModel):
    route: Literal["summary", "tax", "relationship", "current_position", "comparison"]
    queries: list[str] = Field(min_length=1, max_length=3)
    as_of: date | None


class Finding(StrictModel):
    kind: Literal[
        "conclusion",
        "changes",
        "applicability",
        "effective_dates",
        "ca_actions",
        "relationships",
        "comparison",
    ]
    text: str = Field(min_length=1, max_length=700)
    evidence_ids: list[str] = Field(min_length=1, max_length=6)


class Findings(StrictModel):
    findings: list[Finding] = Field(max_length=8)
    missing_information: list[str] = Field(max_length=6)
    insufficient_evidence: bool


class AnswerPlan(StrictModel):
    """Composer orders validated findings; it cannot add uncited claims."""

    finding_ids: list[str] = Field(min_length=1, max_length=16)


class EvidenceReview(StrictModel):
    supported_finding_ids: list[str] = Field(max_length=16)


HEADINGS = {
    "conclusion": "Answer",
    "changes": "What changed",
    "applicability": "Who may be affected",
    "effective_dates": "Dates and deadlines",
    "ca_actions": "CA actions to consider",
    "relationships": "Document relationships",
    "comparison": "Comparison",
}
