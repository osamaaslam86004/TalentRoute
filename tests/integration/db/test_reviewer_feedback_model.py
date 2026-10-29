"""Integration tests for ReviewerFeedback database model and foreign keys."""

import uuid
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models.reviewer_feedback import ReviewerFeedback
from app.db.models.screening import Screening


@pytest.mark.asyncio
async def test_reviewer_feedback_creation_linked_to_screening(
    db_session: AsyncSession,
) -> None:
    """Test creating review and appeal records linked to a Screening."""
    screening = Screening(
        tenant_id="tenant-rev-1",
        application_text="Senior Engineer resume content.",
        job_title="Software Architect",
        required_skills=["Python", "System Design"],
    )
    db_session.add(screening)
    await db_session.flush()

    review = ReviewerFeedback(
        screening_id=screening.id,
        tenant_id="tenant-rev-1",
        reviewer_subject="reviewer_alice",
        reviewer_roles=["reviewer"],
        record_type="review",
        label="agree",
        reason_codes=["evidence_missing"],
        notes="Strong skills matched, but additional evidence needed.",
        rubric_version="screening-rubric-v1",
    )
    db_session.add(review)
    await db_session.flush()

    stmt = select(ReviewerFeedback).where(
        ReviewerFeedback.screening_id == screening.id
    )
    retrieved_review = await db_session.scalar(stmt)

    assert retrieved_review is not None
    assert retrieved_review.reviewer_subject == "reviewer_alice"
    assert retrieved_review.label == "agree"


@pytest.mark.asyncio
async def test_reviewer_feedback_screening_ondelete_set_null(
    db_session: AsyncSession,
) -> None:
    """Test ondelete='SET NULL' behavior when parent Screening is deleted."""
    screening = Screening(
        tenant_id="tenant-rev-2",
        application_text="Mid-level Engineer resume content.",
        job_title="Backend Developer",
        required_skills=["Python"],
    )
    db_session.add(screening)
    await db_session.flush()

    review = ReviewerFeedback(
        screening_id=screening.id,
        tenant_id="tenant-rev-2",
        reviewer_subject="reviewer_bob",
        reviewer_roles=["reviewer"],
        record_type="review",
        label="disagree",
        reason_codes=["role_requirement_mismatch"],
        rubric_version="screening-rubric-v1",
    )
    db_session.add(review)
    await db_session.flush()
    review_id = review.id

    # Delete parent Screening
    await db_session.delete(screening)
    await db_session.flush()

    retrieved_review = await db_session.scalar(
        select(ReviewerFeedback).where(ReviewerFeedback.id == review_id)
    )
    assert retrieved_review is not None
    assert retrieved_review.screening_id is None  # SET NULL verified


@pytest.mark.asyncio
async def test_reviewer_feedback_parent_ondelete_restrict(
    db_session: AsyncSession,
) -> None:
    """Test parent_feedback_id ondelete='RESTRICT' blocks deletion of parent review."""
    parent_review = ReviewerFeedback(
        tenant_id="tenant-rev-3",
        reviewer_subject="reviewer_charlie",
        reviewer_roles=["reviewer"],
        record_type="review",
        label="override_recommendation",
        reason_codes=["policy_or_accessibility_concern"],
        rubric_version="screening-rubric-v1",
    )
    db_session.add(parent_review)
    await db_session.flush()

    appeal_child = ReviewerFeedback(
        tenant_id="tenant-rev-3",
        reviewer_subject="compliance_officer_dan",
        reviewer_roles=["compliance_officer"],
        record_type="appeal",
        parent_feedback_id=parent_review.id,
        appeal_outcome="upheld",
        reason_codes=["policy_or_accessibility_concern"],
        rubric_version="screening-rubric-v1",
    )
    db_session.add(appeal_child)
    await db_session.flush()

    # Attempt to delete parent review with active child appeal
    await db_session.delete(parent_review)
    with pytest.raises(IntegrityError):
        await db_session.flush()
