"""Integration tests for requeue_dead_outbox CLI script."""

import sys
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.db.models.audit_event import AuditEvent
from app.db.models.outbox_event import OutboxEvent
from app.scripts import requeue_dead_outbox
from app.scripts.requeue_dead_outbox import requeue_dead_events


@pytest.mark.asyncio
async def test_requeue_dead_outbox_single_event(db_session) -> None:
    """Test requeuing a single dead-letter event by ID."""
    event_id = uuid.uuid4()
    now = datetime.now(UTC)

    dead_event = OutboxEvent(
        id=event_id,
        tenant_id="tenant-corp",
        event_type="screening.requested",
        aggregate_id=str(uuid.uuid4()),
        payload={"screening_id": str(uuid.uuid4())},
        status="dead",
        attempt_count=12,
        available_at=now,
        locked_at=now,
        dispatched_at=None,
        last_error_code="dispatch_timeout",
    )
    db_session.add(dead_event)
    await db_session.commit()

    requeued_count = await requeue_dead_events(
        event_id=event_id, all_dead=False, limit=100
    )
    assert requeued_count == 1

    await db_session.refresh(dead_event)
    assert dead_event.status == "pending"
    assert dead_event.attempt_count == 0
    assert dead_event.locked_at is None
    assert dead_event.dispatched_at is None
    assert dead_event.last_error_code is None

    audit_row = await db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "outbox.requeued",
            AuditEvent.resource_id == str(event_id),
        )
    )
    assert audit_row is not None
    assert audit_row.tenant_id == "tenant-corp"
    assert audit_row.actor_subject == "service:outbox-recovery"
    assert audit_row.event_metadata["attempt_count"] == 12


@pytest.mark.asyncio
async def test_requeue_dead_outbox_all_dead_with_limit(db_session) -> None:
    """Test requeuing dead-letter events using --all-dead and limit boundary."""
    now = datetime.now(UTC)
    for _ in range(3):
        event = OutboxEvent(
            id=uuid.uuid4(),
            tenant_id="tenant-corp",
            event_type="screening.requested",
            aggregate_id=str(uuid.uuid4()),
            payload={"screening_id": str(uuid.uuid4())},
            status="dead",
            attempt_count=12,
            available_at=now,
        )
        db_session.add(event)
    await db_session.commit()

    requeued_count = await requeue_dead_events(event_id=None, all_dead=True, limit=2)
    assert requeued_count == 2

    dead_count = len(
        list(
            (
                await db_session.scalars(
                    select(OutboxEvent).where(OutboxEvent.status == "dead")
                )
            ).all()
        )
    )
    assert dead_count == 1


@pytest.mark.asyncio
async def test_requeue_dead_outbox_validation_errors(db_session) -> None:
    """Test validation guards in requeue_dead_events."""
    with pytest.raises(
        ValueError, match="choose --event-id or explicitly pass --all-dead"
    ):
        await requeue_dead_events(event_id=None, all_dead=False, limit=100)

    non_existent_id = uuid.uuid4()
    with pytest.raises(ValueError, match="event id was not found in dead-letter state"):
        await requeue_dead_events(event_id=non_existent_id, all_dead=False, limit=100)


def test_requeue_dead_outbox_cli_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test CLI argument parsing and error handling in requeue_dead_outbox.main."""
    monkeypatch.setattr(sys, "argv", ["requeue_dead_outbox"])
    with pytest.raises(SystemExit):
        requeue_dead_outbox.main()

    monkeypatch.setattr(
        sys, "argv", ["requeue_dead_outbox", "--event-id", "invalid-uuid"]
    )
    with pytest.raises(SystemExit):
        requeue_dead_outbox.main()

    monkeypatch.setattr(
        sys, "argv", ["requeue_dead_outbox", "--all-dead", "--limit", "0"]
    )
    with pytest.raises(SystemExit):
        requeue_dead_outbox.main()
