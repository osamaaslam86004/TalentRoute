import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_api_key
from app.db.models.screening import Screening
from app.db.session import get_db_session
from app.repositories.screenings import get_screening, list_screenings
from app.schemas.screening import ScreeningAccepted, ScreeningCreateRequest, ScreeningList, ScreeningRead
from app.workers.tasks import screen_candidate

router = APIRouter(
    prefix="/api/v1/screenings",
    tags=["screenings"],
    dependencies=[Depends(require_api_key)],
)


@router.post("", response_model=ScreeningAccepted, status_code=status.HTTP_202_ACCEPTED)
async def create_screening(
    payload: ScreeningCreateRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> ScreeningAccepted:
    from app.core.config import get_settings
    if len(payload.application_text) > get_settings().max_application_chars:
        raise HTTPException(status_code=413, detail="Application text exceeds configured limit")
    row = Screening(
        status="queued",
        application_text=payload.application_text,
        job_title=payload.job_title,
        required_skills=payload.required_skills,
        human_review_required=True,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    try:
        screen_candidate.delay(str(row.id))
    except Exception as exc:
        row.status = "failed"
        row.error_code = "task_dispatch_failed"
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to queue screening; record was retained for operational reconciliation",
        ) from exc
    return ScreeningAccepted(id=row.id, status=row.status, poll_url=str(request.url_for("get_screening", screening_id=row.id)))


@router.get("", response_model=ScreeningList)
async def get_screenings(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> ScreeningList:
    rows, count = await list_screenings(session, limit=limit, offset=offset)
    return ScreeningList(items=[ScreeningRead.model_validate(row) for row in rows], limit=limit, offset=offset, count=count)


@router.get("/{screening_id}", response_model=ScreeningRead, name="get_screening")
async def get_screening_endpoint(
    screening_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> ScreeningRead:
    row = await get_screening(session, screening_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Screening not found")
    return ScreeningRead.model_validate(row)


@router.delete("/{screening_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_screening(
    screening_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    row = await get_screening(session, screening_id, for_update=True)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Screening not found")
    await session.delete(row)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
