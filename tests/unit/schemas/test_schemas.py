import pytest
from pydantic import ValidationError

from app.schemas.assessment import (
    ExperienceAssessment,
    ExperienceLevel,
    SkillsAssessment,
)
from app.schemas.screening import ScreeningCreateRequest


def test_request_normalizes_and_deduplicates_skills():
    request = ScreeningCreateRequest(
        application_text="  Three years of Python API development experience.  ",
        required_skills=[" Python ", "PostgreSQL", "Python"],
    )
    assert request.application_text.startswith("Three years")
    assert request.required_skills == ["Python", "PostgreSQL"]


def test_request_rejects_blank_skill_set():
    with pytest.raises(ValidationError):
        ScreeningCreateRequest(
            application_text="More than twenty characters of sample text",
            required_skills=[" "],
        )


def test_assessment_confidence_is_bounded():
    with pytest.raises(ValidationError):
        ExperienceAssessment(level=ExperienceLevel.MID_LEVEL, confidence=1.2, rationale="test")


def test_skill_match_uses_controlled_vocabulary():
    with pytest.raises(ValidationError):
        SkillsAssessment(match="Hire now", confidence=0.8, rationale="test")
