"""Integration tests for app/services/screening_service.py (Async Pipeline Service)."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.db.models.audit_event import AuditEvent
from app.db.models.screening import Screening
from app.schemas.assessment import (
    ExperienceAssessment,
    ExperienceLevel,
    Recommendation,
    SkillMatch,
    SkillsAssessment,
)
from app.services.screening_service import process_screening


@pytest_asyncio.fixture
async def pending_screening(db_session) -> Screening:
    """Insert a queued screening record into the database."""
    screening = Screening(
        id=uuid.uuid4(),
        tenant_id="tenant_a",
        request_id="req-12345",
        status="queued",
        application_text=(
            "Jane Doe is a Senior Python Engineer with 8 years of backend experience "
            "building scalable microservices using FastAPI, PostgreSQL, and Docker. "
            "Contact: jane.doe@example.com or 555-0199."
        ),
        job_title="Senior Python Backend Engineer",
        required_skills=["Python", "FastAPI", "PostgreSQL", "Docker"],
        human_review_required=True,
    )
    db_session.add(screening)
    await db_session.commit()
    return screening


@pytest.mark.asyncio
async def test_process_screening_success_flow(
    session_factory,
    pending_screening: Screening,
) -> None:
    """Test successful end-to-end execution of process_screening with mocked graph."""
    mock_exp = ExperienceAssessment(
        level=ExperienceLevel.SENIOR_LEVEL,
        confidence=0.92,
        evidence=["8 years of backend experience building scalable microservices"],
        rationale="Strong senior technical profile.",
    )
    mock_skills = SkillsAssessment(
        match=SkillMatch.MATCH,
        confidence=0.95,
        evidence=["FastAPI", "PostgreSQL", "Docker"],
        gaps=[],
        rationale="All core skills verified.",
    )
    mock_graph = AsyncMock()
    mock_graph.ainvoke.return_value = {
        "experience_assessment": mock_exp,
        "skills_assessment": mock_skills,
        "recommendation": Recommendation.HR_REVIEW,
        "recommendation_reason": "Excellent match for senior backend role.",
    }

    with (
        patch(
            "app.services.screening_service.AsyncSessionLocal",
            session_factory,
        ),
        patch(
            "app.services.screening_service.create_screening_graph",
            return_value=mock_graph,
        ),
    ):
        await process_screening(pending_screening.id)

    # Inspect persisted screening state using a fresh session
    async with session_factory() as session:
        updated = await session.scalar(
            select(Screening).where(Screening.id == pending_screening.id)
        )
        assert updated is not None
        assert updated.status == "completed"
        assert updated.processing_started_at is None
        assert updated.experience_level == "Senior-level"
        assert updated.experience_confidence == 0.92
        assert updated.skills_match == "Match"
        assert updated.skills_confidence == 0.95
        assert updated.recommendation == "hr_review"
        assert updated.recommendation_reason == "Excellent match for senior backend role."
        assert updated.human_review_required is True
        assert updated.processed_at is not None
        assert updated.error_code is None

        # Verify PII redaction summary was stored
        assert updated.redaction_summary is not None
        assert "email" in updated.redaction_summary or "phone" in updated.redaction_summary

        # Verify audit event was created
        audit_event = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.resource_id == str(pending_screening.id),
                AuditEvent.action == "screening.processing_completed",
            )
        )
        assert audit_event is not None
        assert audit_event.tenant_id == "tenant_a"
        assert audit_event.outcome == "success"


@pytest.mark.asyncio
async def test_process_screening_idempotency_and_duplicate_handling(
    session_factory,
    pending_screening: Screening,
) -> None:
    """Test that running process_screening on a completed screening is a safe no-op."""
    async with session_factory() as session:
        row = await session.scalar(
            select(Screening).where(Screening.id == pending_screening.id)
        )
        row.status = "completed"
        await session.commit()

    mock_graph = AsyncMock()
    with (
        patch(
            "app.services.screening_service.AsyncSessionLocal",
            session_factory,
        ),
        patch(
            "app.services.screening_service.create_screening_graph",
            return_value=mock_graph,
        ),
    ):
        await process_screening(pending_screening.id)

    # Graph should not be called for completed screening
    mock_graph.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_process_screening_failure_handling(
    session_factory,
    pending_screening: Screening,
) -> None:
    """Test exception handling during processing transitions status to failed with error audit."""
    mock_graph = AsyncMock()
    mock_graph.ainvoke.side_effect = RuntimeError("LLM provider timeout error")

    with (
        patch(
            "app.services.screening_service.AsyncSessionLocal",
            session_factory,
        ),
        patch(
            "app.services.screening_service.create_screening_graph",
            return_value=mock_graph,
        ),
    ):
        with pytest.raises(RuntimeError, match="LLM provider timeout error"):
            await process_screening(pending_screening.id)

    # Inspect persisted screening and audit state
    async with session_factory() as session:
        failed_screening = await session.scalar(
            select(Screening).where(Screening.id == pending_screening.id)
        )
        assert failed_screening is not None
        assert failed_screening.status == "failed"
        assert failed_screening.processing_started_at is None
        assert failed_screening.error_code == "screening_processing_failed"
        assert failed_screening.processed_at is not None

        # Verify failure audit event was recorded
        audit_event = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.resource_id == str(pending_screening.id),
                AuditEvent.action == "screening.processing_failed",
            )
        )
        assert audit_event is not None
        assert audit_event.tenant_id == "tenant_a"
        assert audit_event.outcome == "failure"


@pytest.mark.asyncio
async def test_process_screening_missing_record_handled_gracefully(
    session_factory,
) -> None:
    """Test that a non-existent screening ID exits safely without raising errors."""
    missing_id = uuid.uuid4()
    with patch(
        "app.services.screening_service.AsyncSessionLocal",
        session_factory,
    ):
        await process_screening(missing_id)
