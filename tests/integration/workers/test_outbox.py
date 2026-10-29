"""Integration tests for transactional outbox dispatcher (app/workers/outbox.py)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from app.db.models.outbox_event import OutboxEvent
from app.workers.outbox import (
    _claim_batch,
    _dispatch_once,
    _mark_dispatched,
    _mark_failed,
    dispatch_pending_outbox,
)


@pytest.mark.asyncio
async def test_outbox_claim_and_dispatch_success(
    db_session, monkeypatch, session_factory, make_outbox_event
) -> None:
    """Verify that a pending outbox event is claimed, dispatched, and state-updated."""
    monkeypatch.setattr("app.workers.outbox.AsyncSessionLocal", session_factory)

    screening_id = str(uuid.uuid4())
    event = make_outbox_event(
        tenant_id="tenant_a",
        event_type="screening.requested",
        screening_id=screening_id,
        status="pending",
    )
    db_session.add(event)
    await db_session.commit()

    mock_apply_async = MagicMock()
    monkeypatch.setattr(
        "app.workers.outbox.screen_candidate.apply_async", mock_apply_async
    )

    summary = await _dispatch_once()

    assert summary == {"claimed": 1, "dispatched": 1, "failed": 0}

    async with session_factory() as session:
        updated_event = await session.get(OutboxEvent, event.id)
        assert updated_event is not None
        assert updated_event.status == "dispatched"
        assert updated_event.dispatched_at is not None
        assert updated_event.locked_at is None
        assert updated_event.last_error_code is None

    mock_apply_async.assert_called_once()
    kwargs = mock_apply_async.call_args.kwargs
    assert kwargs["args"] == [screening_id]
    assert kwargs["task_id"] == str(event.id)
    assert kwargs["headers"]["tenant_id"] == "tenant_a"


@pytest.mark.asyncio
async def test_outbox_max_attempts_dead_letter_transition(
    db_session, monkeypatch, session_factory, make_outbox_event
) -> None:
    """Verify that stale lease events with max attempts transition to 'dead' status."""
    monkeypatch.setattr("app.workers.outbox.AsyncSessionLocal", session_factory)

    stale_time = datetime.now(UTC) - timedelta(seconds=300)
    stale_event = make_outbox_event(
        tenant_id="tenant_a",
        event_type="screening.requested",
        status="dispatching",
        attempt_count=12,
        locked_at=stale_time,
    )
    db_session.add(stale_event)
    await db_session.commit()

    claimed_rows = await _claim_batch(limit=10)

    assert len(claimed_rows) == 0

    async with session_factory() as session:
        updated_event = await session.get(OutboxEvent, stale_event.id)
        assert updated_event is not None
        assert updated_event.status == "dead"
        assert updated_event.locked_at is None
        assert updated_event.last_error_code == "dispatch_lease_expired"


@pytest.mark.asyncio
async def test_outbox_dispatch_failure_retry_and_dead_letter(
    db_session, monkeypatch, session_factory, make_outbox_event
) -> None:
    """Verify outbox error handling for task dispatch failures and terminal max attempts."""
    monkeypatch.setattr("app.workers.outbox.AsyncSessionLocal", session_factory)

    event_id = str(uuid.uuid4())
    event = make_outbox_event(
        tenant_id="tenant_a",
        event_type="screening.requested",
        status="pending",
        attempt_count=0,
    )
    db_session.add(event)
    await db_session.commit()

    mock_apply_async = MagicMock(
        side_effect=RuntimeError("Celery broker connection lost")
    )
    monkeypatch.setattr(
        "app.workers.outbox.screen_candidate.apply_async", mock_apply_async
    )

    summary = await _dispatch_once()

    assert summary == {"claimed": 1, "dispatched": 0, "failed": 1}

    async with session_factory() as session:
        updated_event = await session.get(OutboxEvent, event.id)
        assert updated_event is not None
        assert updated_event.status == "pending"
        assert updated_event.attempt_count == 1
        assert updated_event.last_error_code == "dispatch_RuntimeError"
        assert updated_event.available_at > datetime.now(UTC)

    # Test terminal max_attempts transition
    async with session_factory() as session:
        term_event = await session.get(OutboxEvent, event.id)
        term_event.attempt_count = 12
        term_event.status = "dispatching"
        await session.commit()

    await _mark_failed(str(event.id), 12, "RuntimeError")

    async with session_factory() as session:
        final_event = await session.get(OutboxEvent, event.id)
        assert final_event is not None
        assert final_event.status == "dead"


@pytest.mark.asyncio
async def test_outbox_unsupported_event_type_handling(
    db_session, monkeypatch, session_factory, make_outbox_event
) -> None:
    """Verify that unsupported event types are caught and marked as dispatch failures."""
    monkeypatch.setattr("app.workers.outbox.AsyncSessionLocal", session_factory)

    unsupported_event = make_outbox_event(
        tenant_id="tenant_a",
        event_type="unsupported.notification.type",
        status="pending",
        attempt_count=0,
    )
    db_session.add(unsupported_event)
    await db_session.commit()

    summary = await _dispatch_once()

    assert summary == {"claimed": 1, "dispatched": 0, "failed": 1}

    async with session_factory() as session:
        updated = await session.get(OutboxEvent, unsupported_event.id)
        assert updated is not None
        assert updated.last_error_code == "dispatch_ValueError"


@pytest.mark.asyncio
async def test_dispatch_pending_outbox_celery_task_wrapper(
    monkeypatch, session_factory
) -> None:
    """Verify synchronous Celery task wrapper executes outbox dispatch and disposes engine."""
    monkeypatch.setattr("app.workers.outbox.AsyncSessionLocal", session_factory)

    mock_dispatch = MagicMock(return_value={"claimed": 2, "dispatched": 2, "failed": 0})
    monkeypatch.setattr("app.workers.outbox._dispatch_once", mock_dispatch)

    mock_dispose = MagicMock()
    monkeypatch.setattr(
        "app.db.session.worker_engine.sync_engine.dispose", mock_dispose
    )

    result = dispatch_pending_outbox()

    assert result == {"claimed": 2, "dispatched": 2, "failed": 0}
    mock_dispose.assert_called()
