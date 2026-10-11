"""Human review and appeal records, deliberately separate from model suggestions."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_audit_event
from app.core.config import get_settings
from app.core.security import Principal, require_permission
from app.db.models.reviewer_feedback import ReviewerFeedback
from app.db.session import get_db_session
from app.repositories.screenings import get_screening
from app.reviews.service import (
    append_appeal_outcome,
    append_feedback,
    get_feedback_by_id,
)
from app.schemas.reviewer_feedback import (
    AppealCreate,
    ReviewerFeedbackCreate,
    ReviewerFeedbackRead,
)

router = APIRouter(tags=["human-review"])


@router.post(
    "/api/v1/screenings/{screening_id}/feedback",
    response_model=ReviewerFeedbackRead,
    status_code=status.HTTP_201_CREATED,
)
async def submit_feedback(
    screening_id: uuid.UUID,
    payload: ReviewerFeedbackCreate,
    request: Request,
    principal: Principal = Depends(require_permission("review:write")),
    session: AsyncSession = Depends(get_db_session),
) -> ReviewerFeedbackRead:

    screening = await get_screening(
        session, screening_id, tenant_id=principal.tenant_id
    )

    if screening is None:
        raise HTTPException(status_code=404, detail="Screening not found")

    item = await append_feedback(
        session,
        screening_id=screening.id,
        tenant_id=principal.tenant_id,
        principal=principal,
        payload=payload,
        rubric_version=get_settings().screening_rubric_version,
    )

    await record_audit_event(
        session,
        principal=principal,
        tenant_id=principal.tenant_id,
        request_id=getattr(request.state, "request_id", None),
        action="review.feedback_submitted",
        resource_type="reviewer_feedback",
        resource_id=str(item.id),
        metadata={
            "label": payload.label.value,
            "reason_codes": payload.reason_codes,
            "rubric_version": item.rubric_version,
        },
    )

    await session.commit()
    await session.refresh(item)

    return ReviewerFeedbackRead.model_validate(item)


@router.get(
    "/api/v1/screenings/{screening_id}/feedback",
    response_model=list[ReviewerFeedbackRead],
)
async def list_feedback(
    screening_id: uuid.UUID,
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(require_permission("review:read")),
    session: AsyncSession = Depends(get_db_session),
) -> list[ReviewerFeedbackRead]:
    screening = await get_screening(
        session, screening_id, tenant_id=principal.tenant_id
    )

    if screening is None:
        raise HTTPException(status_code=404, detail="Screening not found")

    items = list(
        (
            await session.scalars(
                select(ReviewerFeedback)
                .where(
                    ReviewerFeedback.tenant_id == principal.tenant_id,
                    ReviewerFeedback.screening_id == screening_id,
                )
                .order_by(ReviewerFeedback.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
    )

    await record_audit_event(
        session,
        principal=principal,
        tenant_id=principal.tenant_id,
        request_id=getattr(request.state, "request_id", None),
        action="review.feedback_listed",
        resource_type="screening",
        resource_id=str(screening_id),
        metadata={"count": len(items), "limit": limit, "offset": offset},
    )

    await session.commit()

    return [ReviewerFeedbackRead.model_validate(item) for item in items]


@router.post(
    "/api/v1/reviews/{feedback_id}/appeals",
    response_model=ReviewerFeedbackRead,
    status_code=status.HTTP_201_CREATED,
)
async def record_appeal_outcome(
    feedback_id: uuid.UUID,
    payload: AppealCreate,
    request: Request,
    principal: Principal = Depends(require_permission("review:appeal")),
    session: AsyncSession = Depends(get_db_session),
) -> ReviewerFeedbackRead:

    original = await get_feedback_by_id(
        session,
        feedback_id=feedback_id,
        tenant_id=principal.tenant_id,
        record_type="review",
    )

    if original is None:
        raise HTTPException(status_code=404, detail="Reviewer feedback not found")

    # Appeal outcomes are new events. The original reviewer feedback is never overwritten.
    item = await append_appeal_outcome(
        session,
        original=original,
        tenant_id=principal.tenant_id,
        principal=principal,
        payload=payload,
    )

    await record_audit_event(
        session,
        principal=principal,
        tenant_id=principal.tenant_id,
        request_id=getattr(request.state, "request_id", None),
        action="review.appeal_recorded",
        resource_type="reviewer_feedback",
        resource_id=str(item.id),
        metadata={"appeal_outcome": payload.outcome.value, "record_type": "appeal"},
    )

    await session.commit()
    await session.refresh(item)

    return ReviewerFeedbackRead.model_validate(item)
