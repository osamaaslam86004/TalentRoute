"""Integration tests for database connection pool listeners and Celery telemetry signals."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from sqlalchemy import create_engine

from app.observability.context import clear_context, current_context
from app.observability.db_pool import register_db_pool_listeners
from app.workers.telemetry import build_task_headers


def test_db_pool_listeners_checkout_checkin_lifecycle() -> None:
    """Verify SQLAlchemy pool listener hooks track connection checkout durations and connect events."""
    engine = create_engine("sqlite:///:memory:")
    register_db_pool_listeners(engine)

    # Acquire connection from pool (triggers checkout & connect listeners)
    conn = engine.connect()
    raw_conn = conn.connection

    # Simulate SQLAlchemy connection record info inspection
    connection_record = MagicMock()
    connection_record.info = {}

    # Fire listeners via engine events
    engine.sync_engine.dispatch.checkout(raw_conn, connection_record, None)
    assert "checkout_time" in connection_record.info
    assert isinstance(connection_record.info["checkout_time"], float)

    # Checkin connection
    engine.sync_engine.dispatch.checkin(raw_conn, connection_record)
    assert "checkout_time" not in connection_record.info

    conn.close()
    engine.dispose()


def test_build_task_headers_privacy_boundary() -> None:
    """Verify build_task_headers copies approved correlation metadata and drops raw payload data."""
    outbox_payload: dict[str, Any] = {
        "request_id": "req-outbox-999",
        "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        "enqueued_at": "2026-10-30T08:00:00Z",
        # Sensitive payload data below
        "screening_id": "screen-123",
        "candidate_name": "Jane Doe",
        "resume_text": "Experienced software engineer with python background...",
        "prompt_variables": {"role": "Senior Engineer"},
    }

    headers = build_task_headers(
        outbox_payload, tenant_id="tenant-outbox-555", event_id="event-777"
    )

    # Approved metadata present
    assert headers["tenant_id"] == "tenant-outbox-555"
    assert headers["outbox_event_id"] == "event-777"
    assert headers["request_id"] == "req-outbox-999"
    assert (
        headers["traceparent"]
        == "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    )
    assert headers["enqueued_at"] == "2026-10-30T08:00:00Z"

    # Arbitrary payload data excluded from Celery headers
    assert "candidate_name" not in headers
    assert "resume_text" not in headers
    assert "prompt_variables" not in headers
    assert "screening_id" not in headers


def test_celery_task_telemetry_lifecycle() -> None:
    """Test Celery task telemetry pre-run and post-run signal lifecycle."""
    from app.observability.celery_signals import (
        finish_task_telemetry,
        start_task_telemetry,
    )

    clear_context()

    # Setup dummy Celery sender task object
    sender = MagicMock()
    sender.name = "app.workers.tasks.screen_candidate"

    request = MagicMock()
    request.id = "celery-task-id-123"
    request.headers = {
        "tenant_id": "tenant-celery-888",
        "request_id": "req-celery-999",
        "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        "enqueued_at": "2026-10-30T08:00:00Z",
    }
    sender.request = request

    # Trigger start_task_telemetry
    start_task_telemetry(
        sender=sender,
        task_id="celery-task-id-123",
        args=("screening-456",),
        kwargs={},
    )

    # Verify context scope was established
    ctx = current_context()
    assert ctx is not None
    assert ctx.tenant_id == "tenant-celery-888"
    assert ctx.request_id == "req-celery-999"
    assert ctx.screening_id == "screening-456"

    # Trigger finish_task_telemetry
    finish_task_telemetry(task_id="celery-task-id-123", state="SUCCESS")

    # Context restored/cleared after task postrun
    clear_context()
    assert current_context() is None
