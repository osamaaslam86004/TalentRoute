from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import Principal, require_permission
from app.db.session import get_db_session
from app.audit.service import list_audit_events, record_audit_event, verify_audit_chain
from app.schemas.audit_event import AuditEventList, AuditEventRead, AuditIntegrityRead

router = APIRouter(prefix="/api/v1/audit-events", tags=["audit"])


@router.get("/integrity", response_model=AuditIntegrityRead)
async def get_audit_integrity(
    request: Request,
    principal: Principal = Depends(require_permission("audit:read")),
    session: AsyncSession = Depends(get_db_session),
) -> AuditIntegrityRead:

    result = await verify_audit_chain(session, tenant_id=principal.tenant_id)

    await record_audit_event(
        session,
        principal=principal,
        tenant_id=principal.tenant_id,
        request_id=getattr(request.state, "request_id", None),
        action="audit.integrity_checked",
        resource_type="audit_chain",
        outcome="success" if result["valid"] else "failure",
        metadata={
            "status": "valid" if result["valid"] else "invalid",
            "count": result["event_count"],
        },
    )
    await session.commit()
    return AuditIntegrityRead(**result)


@router.get("", response_model=AuditEventList)
async def get_audit_events(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    action: str | None = Query(default=None, max_length=120),
    principal: Principal = Depends(require_permission("audit:read")),
    session: AsyncSession = Depends(get_db_session),
) -> AuditEventList:

    items, count = await list_audit_events(
        session,
        tenant_id=principal.tenant_id,
        limit=limit,
        offset=offset,
        action=action,
    )

    await record_audit_event(
        session,
        principal=principal,
        tenant_id=principal.tenant_id,
        request_id=getattr(request.state, "request_id", None),
        action="audit.events_read",
        resource_type="audit_event_collection",
        metadata={"limit": limit, "offset": offset, "count": count},
    )

    await session.commit()

    return AuditEventList(
        items=[AuditEventRead.model_validate(item) for item in items],
        limit=limit,
        offset=offset,
        count=count,
    )
