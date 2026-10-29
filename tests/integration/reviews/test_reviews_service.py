"""Integration tests for app/reviews/service.py (Review and Appeal Service)."""

import uuid
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.security import Principal
from app.db.models.reviewer_feedback import ReviewerFeedback
from app.db.models.screening import Screening
from app.reviews.service import (
    append_appeal_outcome,
    append_feedback,
    get_feedback_by_id,
)
from app.schemas.reviewer_feedback import (
    AppealCreate,
    AppealOutcome,
    ReviewerFeedbackCreate,
    ReviewLabel,
)


@pytest_asyncio.fixture
async def sample_screening(db_session: AsyncSession) -> Screening:
    """Persist a baseline screening record for tenant_a."""
    screening = Screening(
        id=uuid.uuid4(),
        tenant_id="tenant_a",
        status="completed",
        application_text="Senior Python Developer with FastAPI and SQLAlchemy experience.",
        job_title="Lead Engineer",
        required_skills=["Python", "FastAPI", "SQLAlchemy"],
        human_review_required=True,
    )
    db_session.add(screening)
    await db_session.commit()
    return screening


@pytest.mark.asyncio
async def test_append_feedback_success(
    db_session: AsyncSession,
    sample_screening: Screening,
    principal_tenant_a: Principal,
) -> None:
    """Test successful creation and persistence of reviewer feedback."""
    payload = ReviewerFeedbackCreate(
        label=ReviewLabel.AGREE,
        reason_codes=["evidence_missing"],
        notes="Strong technical evidence provided in resume.",
    )
    rubric_version = "screening-rubric-v1"

    item = await append_feedback(
        db_session,
        screening_id=sample_screening.id,
        tenant_id=principal_tenant_a.tenant_id,
        principal=principal_tenant_a,
        payload=payload,
        rubric_version=rubric_version,
    )
    await db_session.commit()

    assert item.id is not None
    assert item.screening_id == sample_screening.id
    assert item.tenant_id == "tenant_a"
    assert item.reviewer_subject == principal_tenant_a.subject
    assert item.reviewer_roles == ["recruiter", "reviewer"]  # Sorted list
    assert item.record_type == "review"
    assert item.label == "agree"
    assert item.reason_codes == ["evidence_missing"]
    assert item.notes == "Strong technical evidence provided in resume."
    assert item.rubric_version == rubric_version
    assert item.parent_feedback_id is None
    assert item.appeal_outcome is None

    # Verify retrieval from database
    persisted = await db_session.scalar(
        select(ReviewerFeedback).where(ReviewerFeedback.id == item.id)
    )
    assert persisted is not None
    assert persisted.record_type == "review"


@pytest.mark.asyncio
async def test_append_appeal_outcome_success(
    db_session: AsyncSession,
    sample_screening: Screening,
    principal_tenant_a: Principal,
    compliance_principal_tenant_a: Principal,
) -> None:
    """Test appending an appeal outcome linked to an original feedback record."""
    initial_payload = ReviewerFeedbackCreate(
        label=ReviewLabel.DISAGREE,
        reason_codes=["role_requirement_mismatch"],
        notes="Candidate lacks required AWS experience.",
    )
    original_feedback = await append_feedback(
        db_session,
        screening_id=sample_screening.id,
        tenant_id=principal_tenant_a.tenant_id,
        principal=principal_tenant_a,
        payload=initial_payload,
        rubric_version="screening-rubric-v1",
    )
    await db_session.commit()

    appeal_payload = AppealCreate(
        outcome=AppealOutcome.UPHELD,
        notes="Appeal review confirmed initial assessment.",
    )

    appeal_item = await append_appeal_outcome(
        db_session,
        original=original_feedback,
        tenant_id=compliance_principal_tenant_a.tenant_id,
        principal=compliance_principal_tenant_a,
        payload=appeal_payload,
    )
    await db_session.commit()

    # Assert new linked appeal record
    assert appeal_item.id is not None
    assert appeal_item.id != original_feedback.id
    assert appeal_item.screening_id == sample_screening.id
    assert appeal_item.tenant_id == "tenant_a"
    assert appeal_item.reviewer_subject == compliance_principal_tenant_a.subject
    assert appeal_item.record_type == "appeal"
    assert appeal_item.label is None
    assert appeal_item.reason_codes == []
    assert appeal_item.notes == "Appeal review confirmed initial assessment."
    assert appeal_item.parent_feedback_id == original_feedback.id
    assert appeal_item.appeal_outcome == "upheld"

    # Assert original feedback record remains unmodified
    refreshed_original = await db_session.scalar(
        select(ReviewerFeedback).where(ReviewerFeedback.id == original_feedback.id)
    )
    assert refreshed_original is not None
    assert refreshed_original.record_type == "review"
    assert refreshed_original.label == "disagree"
    assert refreshed_original.appeal_outcome is None


@pytest.mark.asyncio
async def test_get_feedback_by_id_tenant_isolation(
    db_session: AsyncSession,
    sample_screening: Screening,
    principal_tenant_a: Principal,
) -> None:
    """Test that feedback stored under tenant_a is invisible to tenant_b."""
    payload = ReviewerFeedbackCreate(
        label=ReviewLabel.AGREE,
        reason_codes=["other"],
        notes="Tenant A feedback notes.",
    )
    item = await append_feedback(
        db_session,
        screening_id=sample_screening.id,
        tenant_id=principal_tenant_a.tenant_id,
        principal=principal_tenant_a,
        payload=payload,
        rubric_version="screening-rubric-v1",
    )
    await db_session.commit()

    # Retrieve under owning tenant_a -> Success
    retrieved_a = await get_feedback_by_id(
        db_session, feedback_id=item.id, tenant_id="tenant_a"
    )
    assert retrieved_a is not None
    assert retrieved_a.id == item.id

    # Retrieve under different tenant_b -> Returns None (isolated)
    retrieved_b = await get_feedback_by_id(
        db_session, feedback_id=item.id, tenant_id="tenant_b"
    )
    assert retrieved_b is None


@pytest.mark.asyncio
async def test_get_feedback_by_id_with_record_type_filter(
    db_session: AsyncSession,
    sample_screening: Screening,
    principal_tenant_a: Principal,
    compliance_principal_tenant_a: Principal,
) -> None:
    """Test filtering get_feedback_by_id by explicit record_type."""
    initial_payload = ReviewerFeedbackCreate(
        label=ReviewLabel.OVERRIDE_RECOMMENDATION,
        reason_codes=["policy_or_accessibility_concern"],
    )
    review_item = await append_feedback(
        db_session,
        screening_id=sample_screening.id,
        tenant_id=principal_tenant_a.tenant_id,
        principal=principal_tenant_a,
        payload=initial_payload,
        rubric_version="v1",
    )
    await db_session.commit()

    # Query review item matching record_type="review"
    found = await get_feedback_by_id(
        db_session,
        feedback_id=review_item.id,
        tenant_id="tenant_a",
        record_type="review",
    )
    assert found is not None

    # Query review item with record_type="appeal" -> None
    not_found = await get_feedback_by_id(
        db_session,
        feedback_id=review_item.id,
        tenant_id="tenant_a",
        record_type="appeal",
    )
    assert not_found is None
