"""Append-only human-review feedback and appeal-event factories.

Model-generated assessment fields remain unchanged when a reviewer disagrees. An appeal outcome is a
new linked record; it is not an in-place update. Use the dedicated review permissions and audit every
append. Free-form notes must be treated as sensitive and never copied into audit metadata or logs.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import Principal
from app.db.models.reviewer_feedback import ReviewerFeedback
from app.schemas.reviewer_feedback import AppealCreate, ReviewerFeedbackCreate


async def append_feedback(
    session: AsyncSession,
    *,
    screening_id: uuid.UUID,
    tenant_id: str,
    principal: Principal,
    payload: ReviewerFeedbackCreate,
    rubric_version: str,
) -> ReviewerFeedback:
    item = ReviewerFeedback(
        screening_id=screening_id,
        tenant_id=tenant_id,
        reviewer_subject=principal.subject,
        reviewer_roles=sorted(principal.roles),
        record_type="review",
        label=payload.label.value,
        reason_codes=payload.reason_codes,
        notes=payload.notes,
        rubric_version=rubric_version,
    )
    session.add(item)
    await session.flush()
    return item


async def append_appeal_outcome(
    session: AsyncSession,
    *,
    original: ReviewerFeedback,
    tenant_id: str,
    principal: Principal,
    payload: AppealCreate,
) -> ReviewerFeedback:
    item = ReviewerFeedback(
        screening_id=original.screening_id,
        tenant_id=tenant_id,
        reviewer_subject=principal.subject,
        reviewer_roles=sorted(principal.roles),
        record_type="appeal",
        label=None,
        reason_codes=[],
        notes=payload.notes,
        rubric_version=original.rubric_version,
        parent_feedback_id=original.id,
        appeal_outcome=payload.outcome.value,
    )
    session.add(item)
    await session.flush()
    return item


async def get_feedback_by_id(
    session: AsyncSession, *, feedback_id: uuid.UUID, tenant_id: str, record_type: str | None = None
) -> ReviewerFeedback | None:
    stmt = select(ReviewerFeedback).where(
        ReviewerFeedback.id == feedback_id, ReviewerFeedback.tenant_id == tenant_id
    )
    if record_type:
        stmt = stmt.where(ReviewerFeedback.record_type == record_type)
    return await session.scalar(stmt)
