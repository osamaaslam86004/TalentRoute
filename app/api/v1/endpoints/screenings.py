import hashlib
import json
import uuid

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.rate_limit import enforce_screening_rate_limit
from app.core.security import Principal, require_permission
from app.db.models.outbox_event import OutboxEvent
from app.db.models.screening import Screening
from app.db.session import get_db_session
from app.audit.service import record_audit_event
from app.repositories.screenings import get_screening, list_screenings
from app.schemas.screening import (
    ScreeningAccepted,
    ScreeningCreateRequest,
    ScreeningList,
    ScreeningRead,
)

router = APIRouter(prefix="/api/v1/screenings", tags=["screenings"])


@router.post("", response_model=ScreeningAccepted, status_code=status.HTTP_202_ACCEPTED)
async def create_screening(
    payload: ScreeningCreateRequest,
    request: Request,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]{8,128}$",
    ),
    principal: Principal = Depends(require_permission("screening:write")),
    session: AsyncSession = Depends(get_db_session),
) -> ScreeningAccepted:

    if len(payload.application_text) > get_settings().max_application_chars:
        raise HTTPException(
            status_code=413, detail="Application text exceeds configured limit"
        )

    await enforce_screening_rate_limit(request, principal)

    request_fingerprint = hashlib.sha256(
        json.dumps(
            payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()

    # An idempotency key is scoped to the authenticated tenant. Reusing it for
    # a different request is a conflict rather than a silently different job.
    if idempotency_key:
        existing = await session.scalar(
            select(Screening).where(
                Screening.tenant_id == principal.tenant_id,
                Screening.idempotency_key == idempotency_key,
            )
        )

        if existing is not None:

            if existing.request_fingerprint != request_fingerprint:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Idempotency-Key has already been used with a different request",
                )

            await record_audit_event(
                session,
                principal=principal,
                tenant_id=principal.tenant_id,
                request_id=getattr(request.state, "request_id", None),
                action="screening.idempotency_replayed",
                resource_type="screening",
                resource_id=str(existing.id),
                metadata={"status": existing.status},
            )

            await session.commit()

            return ScreeningAccepted(
                id=existing.id,
                status=existing.status,
                poll_url=str(
                    request.url_for("get_screening", screening_id=existing.id)
                ),
            )

    row = Screening(
        tenant_id=principal.tenant_id,
        request_id=getattr(request.state, "request_id", None),
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint if idempotency_key else None,
        status="queued",
        application_text=payload.application_text,
        job_title=payload.job_title,
        required_skills=payload.required_skills,
        human_review_required=True,
    )

    try:
        session.add(row)
        await session.flush()

        # The outbox row and business record commit together. A separate dispatcher
        # will deliver the event to Celery, avoiding a commit/queue dual-write gap.
        session.add(
            OutboxEvent(
                tenant_id=principal.tenant_id,
                event_type="screening.requested",
                aggregate_id=str(row.id),
                payload={"screening_id": str(row.id)},
                status="pending",
            )
        )

        await record_audit_event(
            session,
            principal=principal,
            tenant_id=principal.tenant_id,
            request_id=getattr(request.state, "request_id", None),
            action="screening.created",
            resource_type="screening",
            resource_id=str(row.id),
            metadata={
                "status": row.status,
                "outbox": "pending",
                "idempotency_enabled": bool(idempotency_key),
            },
        )

        await session.commit()

    except IntegrityError as exc:
        await session.rollback()

        # Two same-key requests can race through the initial lookup. The unique
        # index elects one winner; return it if the normalized payload matches.
        if idempotency_key:
            existing = await session.scalar(
                select(Screening).where(
                    Screening.tenant_id == principal.tenant_id,
                    Screening.idempotency_key == idempotency_key,
                )
            )

            if (
                existing is not None
                and existing.request_fingerprint == request_fingerprint
            ):
                return ScreeningAccepted(
                    id=existing.id,
                    status=existing.status,
                    poll_url=str(
                        request.url_for("get_screening", screening_id=existing.id)
                    ),
                )
            if existing is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Idempotency-Key has already been used with a different request",
                ) from exc
        raise

    await session.refresh(row)

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

    rows, count = await list_screenings(
        session, tenant_id=principal.tenant_id, limit=limit, offset=offset
    )

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
        items=[ScreeningRead.model_validate(row) for row in rows],
        limit=limit,
        offset=offset,
        count=count,
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Screening not found"
        )

    await record_audit_event(
        session,
        principal=principal,
        tenant_id=principal.tenant_id,
        request_id=getattr(request.state, "request_id", None),
        action="screening.read",
        resource_type="screening",
        resource_id=str(row.id),
        metadata={
            "status": row.status,
            "model_version": row.model_version,
            "prompt_version": row.prompt_version,
        },
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
    row = await get_screening(
        session, screening_id, tenant_id=principal.tenant_id, for_update=True
    )

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Screening not found"
        )

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
