"""Unit tests for the candidate screening workflow in app.workflows.screening.

Covers:
- State definitions and transitions in graph.py
- Mock assessment provider implementation conforming to AssessmentProvider protocol
- Happy path routing: HR Review, Recruiter Review, Manual Review
- OpenAIAssessmentProvider initialization and error handling
- Exception propagation during graph execution
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

try:
    import pytest
except ImportError:
    pytest = None

from app.core.config import Settings
from app.schemas.assessment import (
    ExperienceAssessment,
    ExperienceLevel,
    Recommendation,
    SkillMatch,
    SkillsAssessment,
)
from app.workflows.screening.graph import create_screening_graph
from app.workflows.screening.providers import (
    OpenAIAssessmentProvider,
)
from app.workflows.screening.state import ScreeningState


class MockAssessmentProvider:
    """Mock implementation conforming to the AssessmentProvider protocol."""

    def __init__(
        self,
        experience_assessment: ExperienceAssessment | None = None,
        skills_assessment: SkillsAssessment | None = None,
    ) -> None:
        self._experience_assessment = experience_assessment or ExperienceAssessment(
            level=ExperienceLevel.MID_LEVEL,
            confidence=0.85,
            evidence=["5 years software engineering experience"],
            rationale="Explicit work history demonstrates mid-level experience.",
        )
        self._skills_assessment = skills_assessment or SkillsAssessment(
            match=SkillMatch.MATCH,
            confidence=0.90,
            evidence=["Proficient in Python and PostgreSQL"],
            gaps=[],
            rationale="All required skills are supported by evidence.",
        )
        self.assess_experience_called = False
        self.assess_skills_called = False

    async def assess_experience(self, application_text: str) -> ExperienceAssessment:
        self.assess_experience_called = True
        return self._experience_assessment

    async def assess_skills(
        self, application_text: str, job_title: str, required_skills: list[str]
    ) -> SkillsAssessment:
        self.assess_skills_called = True
        return self._skills_assessment


class FaultyAssessmentProvider:
    """Assessment provider mock that raises configured exceptions."""

    def __init__(self, exception_to_raise: Exception) -> None:
        self.exception_to_raise = exception_to_raise

    async def assess_experience(self, application_text: str) -> ExperienceAssessment:
        raise self.exception_to_raise

    async def assess_skills(
        self, application_text: str, job_title: str, required_skills: list[str]
    ) -> SkillsAssessment:
        raise self.exception_to_raise


class TestScreeningWorkflowGraph(unittest.IsolatedAsyncioTestCase):
    """Unit tests for LangGraph state machine routing and node execution."""

    def setUp(self) -> None:
        self.base_state: ScreeningState = {
            "screening_id": "test-screening-123",
            "application_text": "Experienced Python backend engineer with 5 years in FastAPI and PostgreSQL.",
            "job_title": "Senior Python Developer",
            "required_skills": ["Python", "FastAPI", "PostgreSQL"],
        }

    async def test_happy_path_hr_review_route(self) -> None:
        """Verify workflow routes to HR Review when skills match is MATCH."""
        exp = ExperienceAssessment(
            level=ExperienceLevel.MID_LEVEL,
            confidence=0.85,
            evidence=["5 years Python experience"],
            rationale="Mid-level experience supported.",
        )
        skills = SkillsAssessment(
            match=SkillMatch.MATCH,
            confidence=0.90,
            evidence=["Python", "FastAPI", "PostgreSQL"],
            gaps=[],
            rationale="All skills matched.",
        )
        mock_provider = MockAssessmentProvider(exp, skills)
        graph = create_screening_graph(provider=mock_provider)

        result = await graph.ainvoke(self.base_state)

        self.assertTrue(mock_provider.assess_experience_called)
        self.assertTrue(mock_provider.assess_skills_called)
        self.assertEqual(result["recommendation"], Recommendation.HR_REVIEW)
        self.assertTrue(result["human_review_required"])
        self.assertIn("HR", result["recommendation_reason"])
        self.assertIn("experience_assessment", result)
        self.assertIn("skills_assessment", result)
        self.assertEqual(
            result["experience_assessment"].level, ExperienceLevel.MID_LEVEL
        )
        self.assertEqual(result["skills_assessment"].match, SkillMatch.MATCH)

    async def test_happy_path_recruiter_review_route(self) -> None:
        """Verify workflow routes to Recruiter Review for Senior candidate with partial skill match."""
        exp = ExperienceAssessment(
            level=ExperienceLevel.SENIOR_LEVEL,
            confidence=0.92,
            evidence=["10 years engineering leadership"],
            rationale="Senior level supported.",
        )
        skills = SkillsAssessment(
            match=SkillMatch.PARTIAL_MATCH,
            confidence=0.70,
            evidence=["Python"],
            gaps=["FastAPI"],
            rationale="Partial skill match.",
        )
        mock_provider = MockAssessmentProvider(exp, skills)
        graph = create_screening_graph(provider=mock_provider)

        result = await graph.ainvoke(self.base_state)

        self.assertEqual(result["recommendation"], Recommendation.RECRUITER_REVIEW)
        self.assertTrue(result["human_review_required"])
        self.assertIn("recruiter", result["recommendation_reason"].lower())
        self.assertEqual(
            result["experience_assessment"].level, ExperienceLevel.SENIOR_LEVEL
        )
        self.assertEqual(result["skills_assessment"].match, SkillMatch.PARTIAL_MATCH)

    async def test_happy_path_recruiter_review_route_senior_no_match(self) -> None:
        """Verify workflow routes to Recruiter Review for Senior candidate with NO_MATCH skills."""
        exp = ExperienceAssessment(
            level=ExperienceLevel.SENIOR_LEVEL,
            confidence=0.95,
            evidence=["Principal Architect history"],
            rationale="Senior candidate.",
        )
        skills = SkillsAssessment(
            match=SkillMatch.NO_MATCH,
            confidence=0.85,
            evidence=[],
            gaps=["Python", "FastAPI"],
            rationale="No skills matched.",
        )
        mock_provider = MockAssessmentProvider(exp, skills)
        graph = create_screening_graph(provider=mock_provider)

        result = await graph.ainvoke(self.base_state)

        self.assertEqual(result["recommendation"], Recommendation.RECRUITER_REVIEW)
        self.assertTrue(result["human_review_required"])

    async def test_happy_path_manual_review_route_entry_level(self) -> None:
        """Verify workflow routes to Manual Review for Entry-level candidate without full skill match."""
        exp = ExperienceAssessment(
            level=ExperienceLevel.ENTRY_LEVEL,
            confidence=0.80,
            evidence=["Recent graduate"],
            rationale="Entry-level candidate.",
        )
        skills = SkillsAssessment(
            match=SkillMatch.NO_MATCH,
            confidence=0.85,
            evidence=[],
            gaps=["Python", "PostgreSQL"],
            rationale="Skills missing.",
        )
        mock_provider = MockAssessmentProvider(exp, skills)
        graph = create_screening_graph(provider=mock_provider)

        result = await graph.ainvoke(self.base_state)

        self.assertEqual(result["recommendation"], Recommendation.MANUAL_REVIEW)
        self.assertTrue(result["human_review_required"])
        self.assertIn("human review", result["recommendation_reason"].lower())

    async def test_happy_path_manual_review_route_insufficient_evidence(self) -> None:
        """Verify workflow routes to Manual Review when evidence is insufficient."""
        exp = ExperienceAssessment(
            level=ExperienceLevel.INSUFFICIENT_EVIDENCE,
            confidence=0.30,
            evidence=[],
            rationale="Application text is brief.",
        )
        skills = SkillsAssessment(
            match=SkillMatch.INSUFFICIENT_EVIDENCE,
            confidence=0.30,
            evidence=[],
            gaps=["Python"],
            rationale="Cannot determine skills.",
        )
        mock_provider = MockAssessmentProvider(exp, skills)
        graph = create_screening_graph(provider=mock_provider)

        result = await graph.ainvoke(self.base_state)

        self.assertEqual(result["recommendation"], Recommendation.MANUAL_REVIEW)
        self.assertTrue(result["human_review_required"])

    async def test_provider_exception_propagates_through_graph(self) -> None:
        """Verify exception raised by provider during node execution propagates cleanly."""
        timeout_error = TimeoutError("LLM provider connection timed out")
        faulty_provider = FaultyAssessmentProvider(timeout_error)
        graph = create_screening_graph(provider=faulty_provider)

        with self.assertRaises(TimeoutError) as ctx:
            await graph.ainvoke(self.base_state)
        self.assertIn("LLM provider connection timed out", str(ctx.exception))


class TestOpenAIAssessmentProvider(unittest.IsolatedAsyncioTestCase):
    """Unit tests for OpenAIAssessmentProvider adapter and initialization."""

    def test_openai_provider_init_missing_api_key_raises_runtime_error(self) -> None:
        """Verify OpenAIAssessmentProvider raises RuntimeError when OPENAI_API_KEY is not set."""
        mock_settings = MagicMock(spec=Settings)
        mock_settings.openai_api_key = None

        with patch(
            "app.workflows.screening.providers.get_settings", return_value=mock_settings
        ):
            with self.assertRaises(RuntimeError) as ctx:
                OpenAIAssessmentProvider()
            self.assertIn("LLM provider is not configured", str(ctx.exception))

    @patch("app.workflows.screening.providers.ChatOpenAI")
    def test_openai_provider_init_success_with_api_key(
        self, mock_chat_openai: MagicMock
    ) -> None:
        """Verify OpenAIAssessmentProvider initializes successfully when API key is provided."""
        mock_secret = MagicMock()
        mock_secret.get_secret_value.return_value = "sk-test-mock-key-12345"

        mock_settings = MagicMock(spec=Settings)
        mock_settings.openai_api_key = mock_secret
        mock_settings.openai_model = "gpt-4o-mini"
        mock_settings.llm_timeout_seconds = 30.0
        mock_settings.llm_base_url = "https://mock.openai.endpoint/v1"

        with patch(
            "app.workflows.screening.providers.get_settings", return_value=mock_settings
        ):
            provider = OpenAIAssessmentProvider()

            self.assertIsNotNone(provider)
            mock_chat_openai.assert_called_once_with(
                model="gpt-4o-mini",
                api_key="sk-test-mock-key-12345",
                temperature=0,
                timeout=30.0,
                max_retries=2,
                base_url="https://mock.openai.endpoint/v1",
            )

    async def test_openai_provider_assess_experience_success(self) -> None:
        """Verify OpenAIAssessmentProvider.assess_experience invokes chain and parses result."""
        mock_secret = MagicMock()
        mock_secret.get_secret_value.return_value = "sk-test-mock-key"

        mock_settings = MagicMock(spec=Settings)
        mock_settings.openai_api_key = mock_secret
        mock_settings.openai_model = "gpt-4o-mini"
        mock_settings.llm_timeout_seconds = 30.0
        mock_settings.llm_base_url = None

        expected_assessment = ExperienceAssessment(
            level=ExperienceLevel.SENIOR_LEVEL,
            confidence=0.90,
            evidence=["Lead developer for 8 years"],
            rationale="Demonstrated senior level expertise.",
        )

        mock_chain = AsyncMock()
        mock_chain.ainvoke.return_value = expected_assessment

        with (
            patch(
                "app.workflows.screening.providers.get_settings",
                return_value=mock_settings,
            ),
            patch("app.workflows.screening.providers.ChatOpenAI") as mock_chat_openai,
        ):
            mock_llm_instance = MagicMock()
            mock_llm_instance.with_structured_output.return_value = mock_chain
            mock_chat_openai.return_value = mock_llm_instance

            provider = OpenAIAssessmentProvider()
            result = await provider.assess_experience(
                "Lead developer for 8 years at Tech Corp."
            )

            self.assertEqual(result, expected_assessment)
            mock_chain.ainvoke.assert_awaited_once()

    async def test_openai_provider_assess_skills_success(self) -> None:
        """Verify OpenAIAssessmentProvider.assess_skills invokes chain and parses result."""
        mock_secret = MagicMock()
        mock_secret.get_secret_value.return_value = "sk-test-mock-key"

        mock_settings = MagicMock(spec=Settings)
        mock_settings.openai_api_key = mock_secret
        mock_settings.openai_model = "gpt-4o-mini"
        mock_settings.llm_timeout_seconds = 30.0
        mock_settings.llm_base_url = None

        expected_assessment = SkillsAssessment(
            match=SkillMatch.MATCH,
            confidence=0.88,
            evidence=["Built REST APIs with Python"],
            gaps=[],
            rationale="Required skills present.",
        )

        mock_chain = AsyncMock()
        mock_chain.ainvoke.return_value = expected_assessment

        with (
            patch(
                "app.workflows.screening.providers.get_settings",
                return_value=mock_settings,
            ),
            patch("app.workflows.screening.providers.ChatOpenAI") as mock_chat_openai,
        ):
            mock_llm_instance = MagicMock()
            mock_llm_instance.with_structured_output.return_value = mock_chain
            mock_chat_openai.return_value = mock_llm_instance

            provider = OpenAIAssessmentProvider()
            result = await provider.assess_skills(
                "Built REST APIs with Python.", "Python Developer", ["Python"]
            )

            self.assertEqual(result, expected_assessment)
            mock_chain.ainvoke.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
