import pytest

from app.schemas.assessment import ExperienceLevel, Recommendation, SkillMatch
from app.workflows.screening.graph import create_screening_graph
from tests.fakes import FakeAssessmentProvider

BASE_STATE = {
    "screening_id": "test-id",
    "application_text": "Five years building Python backend APIs with PostgreSQL.",
    "job_title": "Python Backend Engineer",
    "required_skills": ["Python", "PostgreSQL"],
}


@pytest.mark.asyncio
async def test_match_routes_to_hr_review_and_requires_human():
    graph = create_screening_graph(FakeAssessmentProvider(match=SkillMatch.MATCH))
    result = await graph.ainvoke(BASE_STATE)
    assert result["recommendation"] == Recommendation.HR_REVIEW
    assert result["human_review_required"] is True


@pytest.mark.asyncio
async def test_senior_candidate_without_skill_match_routes_to_recruiter_review():
    graph = create_screening_graph(
        FakeAssessmentProvider(
            level=ExperienceLevel.SENIOR_LEVEL, match=SkillMatch.PARTIAL_MATCH
        )
    )
    result = await graph.ainvoke(BASE_STATE)
    assert result["recommendation"] == Recommendation.RECRUITER_REVIEW
    assert result["human_review_required"] is True


@pytest.mark.asyncio
async def test_other_cases_route_to_manual_review_not_rejection():
    graph = create_screening_graph(FakeAssessmentProvider(match=SkillMatch.NO_MATCH))
    result = await graph.ainvoke(BASE_STATE)
    assert result["recommendation"] == Recommendation.MANUAL_REVIEW
    assert result["human_review_required"] is True
    assert "reject" not in result["recommendation_reason"].lower()
