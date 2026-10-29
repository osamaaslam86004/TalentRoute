"""Integration tests for LangGraph candidate screening workflow compilation and routing."""

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


@pytest.mark.asyncio
async def test_screening_graph_hr_review_route(
    sample_screening_state: ScreeningState,
) -> None:
    """Verify routing to 'hr_review' when skills_match is SkillMatch.MATCH."""
    mock_provider = MockAssessmentProvider(
        experience_assessment=ExperienceAssessment(
            level=ExperienceLevel.MID_LEVEL,
            confidence=0.85,
            evidence=["3 years of backend development"],
            rationale="Mid-level experience supported by resume.",
        ),
        skills_assessment=SkillsAssessment(
            match=SkillMatch.MATCH,
            confidence=0.92,
            evidence=["Python", "FastAPI"],
            gaps=[],
            rationale="Required skills fully matched.",
        ),
    )

    graph = create_screening_graph(provider=mock_provider)
    result = await graph.ainvoke(sample_screening_state)

    assert result["recommendation"] == Recommendation.HR_REVIEW
    assert result["human_review_required"] is True
    assert (
        "Skills evidence supports prioritizing a human HR review"
        in result["recommendation_reason"]
    )
    assert "experience_assessment" in result
    assert "skills_assessment" in result
    assert result["experience_assessment"].level == ExperienceLevel.MID_LEVEL
    assert result["skills_assessment"].match == SkillMatch.MATCH

    assert len(mock_provider.assess_experience_calls) == 1
    assert len(mock_provider.assess_skills_calls) == 1
    assert (
        mock_provider.assess_experience_calls[0]["application_text"]
        == sample_screening_state["application_text"]
    )


@pytest.mark.asyncio
async def test_screening_graph_recruiter_review_route(
    sample_screening_state: ScreeningState,
) -> None:
    """Verify routing to 'recruiter_review' when experience is Senior-level but skills are not full MATCH."""
    mock_provider = MockAssessmentProvider(
        experience_assessment=ExperienceAssessment(
            level=ExperienceLevel.SENIOR_LEVEL,
            confidence=0.90,
            evidence=["8 years leading software teams"],
            rationale="Demonstrated senior level engineering leadership.",
        ),
        skills_assessment=SkillsAssessment(
            match=SkillMatch.PARTIAL_MATCH,
            confidence=0.75,
            evidence=["Python"],
            gaps=["FastAPI"],
            rationale="Partial match on required skillset.",
        ),
    )

    graph = create_screening_graph(provider=mock_provider)
    result = await graph.ainvoke(sample_screening_state)

    assert result["recommendation"] == Recommendation.RECRUITER_REVIEW
    assert result["human_review_required"] is True
    assert (
        "Experience appears senior while required-skill evidence"
        in result["recommendation_reason"]
    )
    assert result["experience_assessment"].level == ExperienceLevel.SENIOR_LEVEL
    assert result["skills_assessment"].match == SkillMatch.PARTIAL_MATCH


@pytest.mark.asyncio
async def test_screening_graph_manual_review_route(
    sample_screening_state: ScreeningState,
) -> None:
    """Verify routing to 'manual_review' when experience is not Senior and skills are incomplete or insufficient."""
    mock_provider = MockAssessmentProvider(
        experience_assessment=ExperienceAssessment(
            level=ExperienceLevel.ENTRY_LEVEL,
            confidence=0.60,
            evidence=["Recent bootcamp graduate"],
            rationale="Limited commercial work history.",
        ),
        skills_assessment=SkillsAssessment(
            match=SkillMatch.INSUFFICIENT_EVIDENCE,
            confidence=0.40,
            evidence=[],
            gaps=["Python", "FastAPI"],
            rationale="Insufficient evidence in application text.",
        ),
    )

    graph = create_screening_graph(provider=mock_provider)
    result = await graph.ainvoke(sample_screening_state)

    assert result["recommendation"] == Recommendation.MANUAL_REVIEW
    assert result["human_review_required"] is True
    assert (
        "Evidence is insufficient or does not clearly support"
        in result["recommendation_reason"]
    )
    assert result["skills_assessment"].match == SkillMatch.INSUFFICIENT_EVIDENCE


@pytest.mark.asyncio
async def test_screening_graph_node_execution_sequence(
    sample_screening_state: ScreeningState,
) -> None:
    """Verify that nodes execute in order and intermediate states accumulate correctly."""
    call_order: list[str] = []

    class TrackingProvider:
        async def assess_experience(
            self, application_text: str
        ) -> ExperienceAssessment:
            call_order.append("assess_experience")
            return ExperienceAssessment(
                level=ExperienceLevel.MID_LEVEL,
                confidence=0.8,
                evidence=["Dev exp"],
                rationale="Mid-level.",
            )

        async def assess_skills(
            self, application_text: str, job_title: str, required_skills: list[str]
        ) -> SkillsAssessment:
            call_order.append("assess_skills")
            return SkillsAssessment(
                match=SkillMatch.NO_MATCH,
                confidence=0.9,
                evidence=[],
                gaps=["Python"],
                rationale="No match.",
            )

    graph = create_screening_graph(provider=TrackingProvider())
    result = await graph.ainvoke(sample_screening_state)

    assert call_order == ["assess_experience", "assess_skills"]
    assert result["recommendation"] == Recommendation.MANUAL_REVIEW
