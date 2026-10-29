"""Integration and unit tests for AssessmentProvider protocol and OpenAI provider adapter."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.schemas.assessment import (
    ExperienceAssessment,
    ExperienceLevel,
    SkillMatch,
    SkillsAssessment,
)
from app.workflows.screening.prompts import (
    EXPERIENCE_SYSTEM_PROMPT,
    SKILLS_SYSTEM_PROMPT,
)
from app.workflows.screening.providers import (
    AssessmentProvider,
    OpenAIAssessmentProvider,
)


def test_assessment_provider_protocol_compliance() -> None:
    """Verify custom test stubs conform to the AssessmentProvider protocol."""

    class StubProvider:
        async def assess_experience(
            self, application_text: str
        ) -> ExperienceAssessment:
            return ExperienceAssessment(
                level=ExperienceLevel.ENTRY_LEVEL,
                confidence=1.0,
                evidence=[],
                rationale="Stub.",
            )

        async def assess_skills(
            self, application_text: str, job_title: str, required_skills: list[str]
        ) -> SkillsAssessment:
            return SkillsAssessment(
                match=SkillMatch.NO_MATCH,
                confidence=1.0,
                evidence=[],
                gaps=[],
                rationale="Stub.",
            )

    stub = StubProvider()
    assert isinstance(stub, AssessmentProvider)


def test_openai_provider_unconfigured_error() -> None:
    """Verify OpenAIAssessmentProvider raises RuntimeError when API key is missing."""
    mock_settings = Settings(openai_api_key=None)

    with (
        patch(
            "app.workflows.screening.providers.get_settings",
            return_value=mock_settings,
        ),
        pytest.raises(RuntimeError, match="LLM provider is not configured"),
    ):
        OpenAIAssessmentProvider()


@pytest.mark.asyncio
async def test_openai_provider_assess_experience_execution() -> None:
    """Verify OpenAIAssessmentProvider passes correct prompts and parses experience results."""
    mock_settings = Settings(
        openai_api_key=SecretStr("sk-test-key-1234567890"),
        openai_model="gpt-4o-mini",
        llm_timeout_seconds=30.0,
    )

    expected_assessment = ExperienceAssessment(
        level=ExperienceLevel.SENIOR_LEVEL,
        confidence=0.95,
        evidence=["8 years as Tech Lead"],
        rationale="Strong senior experience.",
    )

    mock_llm = MagicMock()
    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(return_value=expected_assessment)
    mock_llm.with_structured_output.return_value = mock_chain

    with (
        patch(
            "app.workflows.screening.providers.get_settings",
            return_value=mock_settings,
        ),
        patch(
            "app.workflows.screening.providers.ChatOpenAI",
            return_value=mock_llm,
        ),
    ):
        provider = OpenAIAssessmentProvider()
        result = await provider.assess_experience("Sample candidate resume text.")

        assert result == expected_assessment
        assert mock_chain.ainvoke.call_count == 1
        call_args = mock_chain.ainvoke.call_args[0][0]
        assert call_args[0] == ("system", EXPERIENCE_SYSTEM_PROMPT)
        assert "Sample candidate resume text." in call_args[1][1]


@pytest.mark.asyncio
async def test_openai_provider_assess_skills_execution() -> None:
    """Verify OpenAIAssessmentProvider formats job title and required skills in prompt."""
    mock_settings = Settings(
        openai_api_key=SecretStr("sk-test-key-1234567890"),
        openai_model="gpt-4o-mini",
        llm_timeout_seconds=30.0,
    )

    expected_assessment = SkillsAssessment(
        match=SkillMatch.MATCH,
        confidence=0.88,
        evidence=["Python", "FastAPI"],
        gaps=[],
        rationale="Matches all required skills.",
    )

    mock_llm = MagicMock()
    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(return_value=expected_assessment)
    mock_llm.with_structured_output.return_value = mock_chain

    with (
        patch(
            "app.workflows.screening.providers.get_settings",
            return_value=mock_settings,
        ),
        patch(
            "app.workflows.screening.providers.ChatOpenAI",
            return_value=mock_llm,
        ),
    ):
        provider = OpenAIAssessmentProvider()
        result = await provider.assess_skills(
            application_text="Python and FastAPI developer.",
            job_title="Backend Engineer",
            required_skills=["Python", "FastAPI"],
        )

        assert result == expected_assessment
        assert mock_chain.ainvoke.call_count == 1
        call_args = mock_chain.ainvoke.call_args[0][0]
        assert call_args[0] == ("system", SKILLS_SYSTEM_PROMPT)
        prompt_text = call_args[1][1]
        assert "Job title: Backend Engineer" in prompt_text
        assert "Required skills: Python, FastAPI" in prompt_text
