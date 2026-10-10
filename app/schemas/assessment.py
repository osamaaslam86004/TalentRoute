from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ExperienceLevel(StrEnum):
    ENTRY_LEVEL = "Entry-level"
    MID_LEVEL = "Mid-level"
    SENIOR_LEVEL = "Senior-level"
    INSUFFICIENT_EVIDENCE = "Insufficient evidence"


class SkillMatch(StrEnum):
    MATCH = "Match"
    PARTIAL_MATCH = "Partial match"
    NO_MATCH = "No match"
    INSUFFICIENT_EVIDENCE = "Insufficient evidence"


class Recommendation(StrEnum):
    HR_REVIEW = "hr_review"
    RECRUITER_REVIEW = "recruiter_review"
    MANUAL_REVIEW = "manual_review"


class ExperienceAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: ExperienceLevel
    confidence: float = Field(ge=0.0, le=1.0, description="Model estimate, not a calibrated probability.")
    evidence: list[str] = Field(default_factory=list, max_length=8)
    rationale: str = Field(max_length=1200)

    @field_validator("evidence")
    @classmethod
    def cap_evidence_length(cls, values: list[str]) -> list[str]:
        return [v[:500] for v in values]


class SkillsAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    match: SkillMatch
    confidence: float = Field(ge=0.0, le=1.0, description="Model estimate, not a calibrated probability.")
    evidence: list[str] = Field(default_factory=list, max_length=12)
    gaps: list[str] = Field(default_factory=list, max_length=20)
    rationale: str = Field(max_length=1600)

    @field_validator("evidence")
    @classmethod
    def cap_evidence_length(cls, values: list[str]) -> list[str]:
        return [v[:500] for v in values]
