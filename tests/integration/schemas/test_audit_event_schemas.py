"""Integration tests for audit event schemas, metadata aliases, ORM round-trips, and endpoint payloads."""

import uuid
from datetime import UTC, datetime
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.audit_event import AuditEvent
from app.schemas.audit_event import (
    AuditEventList,
    AuditEventRead,
    AuditIntegrityRead,
)


def test_audit_event_read_metadata_alias():
    event_id = uuid.uuid4()
    now = datetime.now(UTC)
    read_schema = AuditEventRead(
        id=event_id,
        occurred_at=now,
        tenant_id="tenant-schema-test",
        sequence_no=1,
        previous_hash="0" * 64,
        event_hash="a" * 64,
        actor_subject="usr-admin-1",
        actor_roles=["admin"],
        request_id="req-999",
        action="screening.created",
        resource_type="screening",
        resource_id=str(event_id),
        outcome="success",
        event_metadata={"status": "queued", "count": 1},
    )
    exported = read_schema.model_dump(by_alias=True)
    assert "metadata" in exported
    assert exported["metadata"] == {"status": "queued", "count": 1}
    assert "event_metadata" not in exported


@pytest.mark.asyncio
async def test_audit_event_read_orm_round_trip(db_session: AsyncSession):
    event_id = uuid.uuid4()
    now = datetime.now(UTC)
    orm_audit = AuditEvent(
        id=event_id,
        occurred_at=now,
        tenant_id="tenant-schema-test",
        sequence_no=1,
        previous_hash="0" * 64,
        event_hash="b" * 64,
        actor_subject="usr-admin-1",
        actor_roles=["admin"],
        request_id="req-123",
        action="audit.test_action",
        resource_type="audit_chain",
        resource_id="res-123",
        outcome="success",
        event_metadata={"schema_valid": True, "count": 5},
    )
    db_session.add(orm_audit)
    await db_session.commit()
    await db_session.refresh(orm_audit)

    schema_read = AuditEventRead.model_validate(orm_audit)
    assert schema_read.id == event_id
    assert schema_read.tenant_id == "tenant-schema-test"
    assert schema_read.sequence_no == 1
    assert schema_read.event_metadata == {"schema_valid": True, "count": 5}

    exported = schema_read.model_dump(by_alias=True)
    assert exported["metadata"] == {"schema_valid": True, "count": 5}


@pytest.mark.asyncio
async def test_audit_endpoints_schema_integration(
    async_client: AsyncClient,
    db_session: AsyncSession,
):
    # Test GET /api/v1/audit-events/integrity endpoint schema serialization
    response = await async_client.get("/api/v1/audit-events/integrity")
    assert response.status_code == 200
    integrity_data = response.json()
    integrity_read = AuditIntegrityRead(**integrity_data)
    assert integrity_read.tenant_id == "tenant-schema-test"
    assert integrity_read.valid is True

    # Test GET /api/v1/audit-events list schema serialization
    response = await async_client.get("/api/v1/audit-events")
    assert response.status_code == 200
    events_data = response.json()
    events_list = AuditEventList(**events_data)
    assert isinstance(events_list.items, list)
    assert events_list.limit == 100

    # Test invalid pagination limit parameter -> 422
    invalid_response = await async_client.get("/api/v1/audit-events?limit=0")
    assert invalid_response.status_code == 422
