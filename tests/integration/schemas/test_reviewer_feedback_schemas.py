"""Integration tests for reviewer feedback schemas, reason codes, ORM round-trips, and endpoint payloads."""

import uuid
import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.reviewer_feedback import ReviewerFeedback
from app.db.models.screening import Screening
from app.schemas.reviewer_feedback import (
    ALLOWED_REASON_CODES,
    AppealCreate,
    AppealOutcome,
    ReviewerFeedbackCreate,
    ReviewerFeedbackRead,
    ReviewLabel,
)


def test_reviewer_feedback_create_valid():
    payload = ReviewerFeedbackCreate(
        label=ReviewLabel.AGREE,
        reason_codes=[" evidence_missing ", "role_requirement_mismatch"],
        notes="Detailed reviewer assessment notes.",
    )
    assert payload.label == ReviewLabel.AGREE
    assert payload.reason_codes == ["evidence_missing", "role_requirement_mismatch"]
    assert payload.notes == "Detailed reviewer assessment notes."


def test_reviewer_feedback_create_unapproved_reason_code():
    with pytest.raises(ValidationError) as exc_info:
        ReviewerFeedbackCreate(
            label=ReviewLabel.DISAGREE,
            reason_codes=["evidence_missing", "unapproved_reason_code"],
        )
    assert "Unsupported reason code(s)" in str(exc_info.value)


def test_reviewer_feedback_create_empty_reason_codes():
    with pytest.raises(ValidationError):
        ReviewerFeedbackCreate(
            label=ReviewLabel.AGREE,
            reason_codes=["   ", ""],
        )


def test_reviewer_feedback_create_forbidden_extra_fields():
    with pytest.raises(ValidationError):
        ReviewerFeedbackCreate(
            label=ReviewLabel.AGREE,
            reason_codes=["evidence_missing"],
            extra_prop="forbidden",
        )


def test_appeal_create_valid_and_invalid():
    appeal = AppealCreate(
        outcome=AppealOutcome.UPHELD,
        notes="Appeal upheld by compliance officer.",
    )
    assert appeal.outcome == AppealOutcome.UPHELD

    with pytest.raises(ValidationError):
        AppealCreate(
            outcome="invalid_outcome",  # type: ignore
        )

    with pytest.raises(ValidationError):
        AppealCreate(
            outcome=AppealOutcome.REVISED,
            unexpected="field",
        )


@pytest.mark.asyncio
async def test_reviewer_feedback_read_orm_round_trip(db_session: AsyncSession):
    screening_id = uuid.uuid4()
    screening = Screening(
        id=screening_id,
        tenant_id="tenant-schema-test",
        status="completed",
        application_text="Valid application text exceeding twenty characters.",
        job_title="Developer",
        required_skills=["Python"],
        human_review_required=True,
    )
    db_session.add(screening)
    await db_session.flush()

    feedback_id = uuid.uuid4()
    orm_feedback = ReviewerFeedback(
        id=feedback_id,
        screening_id=screening_id,
        tenant_id="tenant-schema-test",
        reviewer_subject="usr-reviewer-99",
        reviewer_roles=["reviewer"],
        record_type="review",
        label="agree",
        reason_codes=["evidence_missing"],
        notes="Agreed with model assessment.",
        rubric_version="screening-rubric-v1",
    )
    db_session.add(orm_feedback)
    await db_session.commit()
    await db_session.refresh(orm_feedback)

    schema_read = ReviewerFeedbackRead.model_validate(orm_feedback)
    assert schema_read.id == feedback_id
    assert schema_read.screening_id == screening_id
    assert schema_read.label == ReviewLabel.AGREE
    assert schema_read.reason_codes == ["evidence_missing"]
    assert schema_read.rubric_version == "screening-rubric-v1"


@pytest.mark.asyncio
async def test_submit_feedback_endpoint_integration(
    async_client: AsyncClient,
    db_session: AsyncSession,
):
    screening_id = uuid.uuid4()
    screening = Screening(
        id=screening_id,
        tenant_id="tenant-schema-test",
        status="completed",
        application_text="Valid application text exceeding twenty characters.",
        job_title="Developer",
        required_skills=["Python"],
        human_review_required=True,
    )
    db_session.add(screening)
    await db_session.commit()

    # Successful feedback submission
    response = await async_client.post(
        f"/api/v1/screenings/{screening_id}/feedback",
        json={
            "label": "disagree",
            "reason_codes": ["role_requirement_mismatch"],
            "notes": "Candidate lacks required senior experience.",
        },
    )
    assert response.status_code == 201
    data = response.json()
    feedback_read = ReviewerFeedbackRead(**data)
    assert feedback_read.label == ReviewLabel.DISAGREE
    assert feedback_read.reason_codes == ["role_requirement_mismatch"]

    # Invalid feedback submission with unapproved reason code -> 422
    response_invalid = await async_client.post(
        f"/api/v1/screenings/{screening_id}/feedback",
        json={
            "label": "agree",
            "reason_codes": ["invalid_unapproved_code"],
        },
    )
    assert response_invalid.status_code == 422


@pytest.mark.asyncio
async def test_record_appeal_endpoint_integration(
    async_client: AsyncClient,
    db_session: AsyncSession,
):
    screening_id = uuid.uuid4()
    screening = Screening(
        id=screening_id,
        tenant_id="tenant-schema-test",
        status="completed",
        application_text="Valid application text exceeding twenty characters.",
        job_title="Developer",
        required_skills=["Python"],
        human_review_required=True,
    )
    db_session.add(screening)
    await db_session.flush()

    feedback_id = uuid.uuid4()
    orm_feedback = ReviewerFeedback(
        id=feedback_id,
        screening_id=screening_id,
        tenant_id="tenant-schema-test",
        reviewer_subject="usr-reviewer-99",
        reviewer_roles=["reviewer"],
        record_type="review",
        label="agree",
        reason_codes=["evidence_missing"],
        rubric_version="screening-rubric-v1",
    )
    db_session.add(orm_feedback)
    await db_session.commit()

    # Successful appeal recording
    response = await async_client.post(
        f"/api/v1/reviews/{feedback_id}/appeals",
        json={
            "outcome": "revised",
            "notes": "Re-reviewed under updated rubric.",
        },
    )
    assert response.status_code == 201
    data = response.json()
    appeal_read = ReviewerFeedbackRead(**data)
    assert appeal_read.appeal_outcome == AppealOutcome.REVISED
    assert appeal_read.parent_feedback_id == feedback_id

    # Invalid appeal recording -> 422
    response_invalid = await async_client.post(
        f"/api/v1/reviews/{feedback_id}/appeals",
        json={
            "outcome": "unknown_appeal_outcome",
        },
    )
    assert response_invalid.status_code == 422
