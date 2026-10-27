import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ReviewLabel(StrEnum):
    AGREE = "agree"
    DISAGREE = "disagree"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    OVERRIDE_RECOMMENDATION = "override_recommendation"


class AppealOutcome(StrEnum):
    PENDING = "pending"
    UPHELD = "upheld"
    REVISED = "revised"


ALLOWED_REASON_CODES = {
    "evidence_missing",
    "evidence_conflicts",
    "role_requirement_mismatch",
    "redaction_review_needed",
    "policy_or_accessibility_concern",
    "rubric_ambiguity",
    "other",
}

NonEmpty = Annotated[str, Field(min_length=1, max_length=80)]


class ReviewerFeedbackCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    label: ReviewLabel
    reason_codes: list[NonEmpty] = Field(min_length=1, max_length=8)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("reason_codes")
    @classmethod
    def validate_reason_codes(cls, values: list[str]) -> list[str]:

        unique = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        unknown = set(unique).difference(ALLOWED_REASON_CODES)

        if unknown:
            raise ValueError(
                f"Unsupported reason code(s): {', '.join(sorted(unknown))}"
            )

        if not unique:
            raise ValueError("At least one reason code is required")

        return unique


class AppealCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    outcome: AppealOutcome
    notes: str | None = Field(default=None, max_length=2000)


class ReviewerFeedbackRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    screening_id: uuid.UUID | None
    tenant_id: str
    reviewer_subject: str
    reviewer_roles: list[str]
    record_type: str
    label: ReviewLabel | None
    reason_codes: list[str]
    notes: str | None
    rubric_version: str
    parent_feedback_id: uuid.UUID | None
    appeal_outcome: AppealOutcome | None
    created_at: datetime
