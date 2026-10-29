"""Integration tests for OutboxEvent database model."""

import uuid
from datetime import UTC, datetime
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models.outbox_event import OutboxEvent


@pytest.mark.asyncio
async def test_outbox_event_creation_and_json_payload(
    db_session: AsyncSession,
) -> None:
    """Test transactional outbox record creation with default status and payload."""
    event_id = uuid.uuid4()
    aggregate_id = str(uuid.uuid4())
    payload_data = {
        "screening_id": aggregate_id,
        "request_id": "req-outbox-101",
        "enqueued_at": datetime.now(UTC).isoformat(),
        "trace_parent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
    }

    outbox_event = OutboxEvent(
        id=event_id,
        tenant_id="tenant-outbox-1",
        event_type="screening.requested",
        aggregate_id=aggregate_id,
        payload=payload_data,
    )

    db_session.add(outbox_event)
    await db_session.flush()

    stmt = select(OutboxEvent).where(OutboxEvent.id == event_id)
    retrieved = await db_session.scalar(stmt)

    assert retrieved is not None
    assert retrieved.status == "pending"
    assert retrieved.attempt_count == 0
    assert retrieved.payload["screening_id"] == aggregate_id
    assert retrieved.payload["trace_parent"].startswith("00-4bf92f3577b34da6a3")


@pytest.mark.asyncio
async def test_outbox_event_indexes_and_availability_filtering(
    db_session: AsyncSession,
) -> None:
    """Verify availability and tenant indexing queries on OutboxEvent."""
    tenant = "tenant-outbox-2"
    e1 = OutboxEvent(
        tenant_id=tenant,
        event_type="screening.created",
        aggregate_id="agg-1",
        payload={"data": 1},
        status="pending",
    )
    e2 = OutboxEvent(
        tenant_id=tenant,
        event_type="screening.created",
        aggregate_id="agg-2",
        payload={"data": 2},
        status="dispatched",
    )
    db_session.add_all([e1, e2])
    await db_session.flush()

    stmt = (
        select(OutboxEvent)
        .where(
            OutboxEvent.tenant_id == tenant,
            OutboxEvent.status == "pending",
        )
        .order_by(OutboxEvent.available_at.asc())
    )
    pending_events = (await db_session.scalars(stmt)).all()

    assert len(pending_events) == 1
    assert pending_events[0].aggregate_id == "agg-1"

    index_names = {idx.name for idx in OutboxEvent.__table__.indexes}
    assert "ix_outbox_events_status_available" in index_names
    assert "ix_outbox_events_tenant_created" in index_names
