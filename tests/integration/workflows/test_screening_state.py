"""Data integrity and state propagation tests for ScreeningState and schemas."""

import pytest

from app.schemas.assessment import (
    ExperienceAssessment,
    ExperienceLevel,
    Recommendation,
    SkillMatch,
    SkillsAssessment,
)
from app.workflows.screening.graph import create_screening_graph
from app.workflows.screening.state import ScreeningState
from tests.integration.workflows.conftest import MockAssessmentProvider


def test_screening_state_typed_dict_keys() -> None:
    """Verify ScreeningState required and optional keys."""
    state: ScreeningState = {
        "screening_id": "scr-999",
        "application_text": "Sample text",
        "job_title": "Developer",
        "required_skills": ["Python"],
    }
    assert state["screening_id"] == "scr-999"
    assert state["application_text"] == "Sample text"
    assert state["job_title"] == "Developer"
    assert state["required_skills"] == ["Python"]


@pytest.mark.asyncio
async def test_screening_state_propagation_through_graph(
    sample_screening_state: ScreeningState,
) -> None:
    """Validate that graph state accumulates all required keys through node execution."""
    exp_obj = ExperienceAssessment(
        level=ExperienceLevel.SENIOR_LEVEL,
        confidence=0.92,
        evidence=["Principal engineer for 6 years"],
        rationale="Senior engineering background.",
    )
    skills_obj = SkillsAssessment(
        match=SkillMatch.MATCH,
        confidence=0.95,
        evidence=["Python", "FastAPI", "PostgreSQL"],
        gaps=[],
        rationale="Complete skill overlap.",
    )
    mock_provider = MockAssessmentProvider(
        experience_assessment=exp_obj,
        skills_assessment=skills_obj,
    )

    graph = create_screening_graph(provider=mock_provider)
    output_state = await graph.ainvoke(sample_screening_state)

    # Initial state fields preserved
    assert output_state["screening_id"] == sample_screening_state["screening_id"]
    assert (
        output_state["application_text"] == sample_screening_state["application_text"]
    )
    assert output_state["job_title"] == sample_screening_state["job_title"]
    assert output_state["required_skills"] == sample_screening_state["required_skills"]

    # Accumulated node outputs
    assert "experience_assessment" in output_state
    assert output_state["experience_assessment"] == exp_obj
    assert "skills_assessment" in output_state
    assert output_state["skills_assessment"] == skills_obj
    assert "recommendation" in output_state
    assert isinstance(output_state["recommendation"], Recommendation)
    assert "recommendation_reason" in output_state
    assert isinstance(output_state["recommendation_reason"], str)
    assert output_state["human_review_required"] is True
