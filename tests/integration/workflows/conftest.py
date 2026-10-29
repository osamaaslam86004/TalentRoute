"""Fixtures and mock provider stubs for LangGraph screening workflow integration tests."""

from typing import Any
import pytest
from app.schemas.assessment import (
    ExperienceAssessment,
    ExperienceLevel,
    SkillMatch,
    SkillsAssessment,
)
from app.workflows.screening.providers import AssessmentProvider
from app.workflows.screening.state import ScreeningState


class MockAssessmentProvider:
    """Configurable test double implementing the AssessmentProvider protocol."""

    def __init__(
        self,
        experience_assessment: ExperienceAssessment | None = None,
        skills_assessment: SkillsAssessment | None = None,
    ) -> None:
        self.experience_assessment = experience_assessment or ExperienceAssessment(
            level=ExperienceLevel.MID_LEVEL,
            confidence=0.85,
            evidence=["3 years of Python development"],
            rationale="Demonstrates mid-level software engineering experience.",
        )
        self.skills_assessment = skills_assessment or SkillsAssessment(
            match=SkillMatch.MATCH,
            confidence=0.90,
            evidence=["Python", "FastAPI", "SQLAlchemy"],
            gaps=[],
            rationale="All core skills supported by application text.",
        )
        self.assess_experience_calls: list[dict[str, Any]] = []
        self.assess_skills_calls: list[dict[str, Any]] = []

    async def assess_experience(
        self, application_text: str
    ) -> ExperienceAssessment:
        self.assess_experience_calls.append(
            {"application_text": application_text}
        )
        return self.experience_assessment

    async def assess_skills(
        self, application_text: str, job_title: str, required_skills: list[str]
    ) -> SkillsAssessment:
        self.assess_skills_calls.append(
            {
                "application_text": application_text,
                "job_title": job_title,
                "required_skills": required_skills,
            }
        )
        return self.skills_assessment


@pytest.fixture
def mock_provider() -> MockAssessmentProvider:
    return MockAssessmentProvider()


@pytest.fixture
def sample_screening_state() -> ScreeningState:
    return {
        "screening_id": "test-screening-12345",
        "application_text": (
            "I have 5 years of software development experience specializing in "
            "Python, FastAPI, Docker, and PostgreSQL database optimizations."
        ),
        "job_title": "Senior Python Developer",
        "required_skills": ["Python", "FastAPI", "PostgreSQL"],
    }
