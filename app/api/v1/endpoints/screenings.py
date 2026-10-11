import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import Principal, require_permission
from app.db.models.screening import Screening
from app.db.session import get_db_session
from app.future_features.audit_events import record_audit_event
from app.repositories.screenings import get_screening, list_screenings
from app.schemas.screening import ScreeningAccepted, ScreeningCreateRequest, ScreeningList, ScreeningRead
from app.workers.tasks import screen_candidate

router = APIRouter(prefix="/api/v1/screenings", tags=["screenings"])


@router.post("", response_model=ScreeningAccepted, status_code=status.HTTP_202_ACCEPTED)
async def create_screening(
    payload: ScreeningCreateRequest,
    request: Request,
    principal: Principal = Depends(require_permission("screening:write")),
    session: AsyncSession = Depends(get_db_session),
) -> ScreeningAccepted:
    if len(payload.application_text) > get_settings().max_application_chars:
        raise HTTPException(status_code=413, detail="Application text exceeds configured limit")

    row = Screening(
        tenant_id=principal.tenant_id,
        request_id=getattr(request.state, "request_id", None),
        status="queued",
        application_text=payload.application_text,
        job_title=payload.job_title,
        required_skills=payload.required_skills,
        human_review_required=True,
    )
    session.add(row)
    await session.flush()
    await record_audit_event(
        session,
        principal=principal,
        tenant_id=principal.tenant_id,
        request_id=getattr(request.state, "request_id", None),
        action="screening.created",
        resource_type="screening",
        resource_id=str(row.id),
        metadata={"status": row.status},
    )
    await session.commit()
    await session.refresh(row)

    try:
        screen_candidate.delay(str(row.id))
    except Exception as exc:
        row.status = "failed"
        row.error_code = "task_dispatch_failed"
        await record_audit_event(
            session,
            principal=principal,
            tenant_id=principal.tenant_id,
            request_id=getattr(request.state, "request_id", None),
            action="screening.dispatch_failed",
            resource_type="screening",
            resource_id=str(row.id),
            outcome="failure",
            metadata={"status": row.status},
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to queue screening; record was retained for operational reconciliation",
        ) from exc

    return ScreeningAccepted(
        id=row.id,
        status=row.status,
        poll_url=str(request.url_for("get_screening", screening_id=row.id)),
    )


@router.get("", response_model=ScreeningList)
async def get_screenings(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(require_permission("screening:read")),
    session: AsyncSession = Depends(get_db_session),
) -> ScreeningList:
    rows, count = await list_screenings(session, tenant_id=principal.tenant_id, limit=limit, offset=offset)
    await record_audit_event(
        session,
        principal=principal,
        tenant_id=principal.tenant_id,
        request_id=getattr(request.state, "request_id", None),
        action="screening.listed",
        resource_type="screening_collection",
        metadata={"limit": limit, "offset": offset, "count": count},
    )
    await session.commit()
    return ScreeningList(
        items=[ScreeningRead.model_validate(row) for row in rows], limit=limit, offset=offset, count=count
    )


@router.get("/{screening_id}", response_model=ScreeningRead, name="get_screening")
async def get_screening_endpoint(
    screening_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_permission("screening:read")),
    session: AsyncSession = Depends(get_db_session),
) -> ScreeningRead:
    row = await get_screening(session, screening_id, tenant_id=principal.tenant_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Screening not found")
    await record_audit_event(
        session,
        principal=principal,
        tenant_id=principal.tenant_id,
        request_id=getattr(request.state, "request_id", None),
        action="screening.read",
        resource_type="screening",
        resource_id=str(row.id),
        metadata={"status": row.status, "model_version": row.model_version, "prompt_version": row.prompt_version},
    )
    await session.commit()
    return ScreeningRead.model_validate(row)


@router.delete("/{screening_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_screening(
    screening_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_permission("screening:delete")),
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    row = await get_screening(session, screening_id, tenant_id=principal.tenant_id, for_update=True)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Screening not found")
    await record_audit_event(
        session,
        principal=principal,
        tenant_id=principal.tenant_id,
        request_id=getattr(request.state, "request_id", None),
        action="screening.deleted",
        resource_type="screening",
        resource_id=str(row.id),
        metadata={"status": row.status},
    )
    await session.delete(row)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
