import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.assessment import ExperienceLevel, Recommendation, SkillMatch

NonEmpty = Annotated[str, Field(min_length=1, max_length=500)]


class ScreeningCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    application_text: str = Field(min_length=20, max_length=100000)
    job_title: str = Field(default="Python Developer", min_length=2, max_length=160)
    required_skills: list[NonEmpty] = Field(
        default_factory=lambda: ["Python"], min_length=1, max_length=30
    )

    @field_validator("application_text")
    @classmethod
    def normalize_application(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("application_text must not be blank")
        return value

    @field_validator("required_skills")
    @classmethod
    def normalize_skills(cls, values: list[str]) -> list[str]:
        deduped = list(dict.fromkeys(v.strip() for v in values if v.strip()))
        if not deduped:
            raise ValueError("required_skills must contain at least one non-blank skill")
        return deduped


class ScreeningAccepted(BaseModel):
    id: uuid.UUID
    status: str
    poll_url: str


class ScreeningRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    job_title: str
    required_skills: list[str]
    experience_level: ExperienceLevel | None = None
    experience_confidence: float | None = None
    experience_evidence: list[str] | None = None
    skills_match: SkillMatch | None = None
    skills_confidence: float | None = None
    skills_evidence: list[str] | None = None
    skill_gaps: list[str] | None = None
    recommendation: Recommendation | None = None
    recommendation_reason: str | None = None
    human_review_required: bool
    error_code: str | None = None
    attempt_count: int
    created_at: datetime
    updated_at: datetime
    processed_at: datetime | None = None


class ScreeningList(BaseModel):
    items: list[ScreeningRead]
    limit: int
    offset: int
    count: int
