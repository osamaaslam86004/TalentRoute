"""Unit and integration tests for telemetry header filtering (app/workers/telemetry.py)."""
from __future__ import annotations

from app.workers.telemetry import build_task_headers


def test_build_task_headers_filtering():
    """Verify build_task_headers filters correlation keys and sanitizes values."""
    payload = {
        "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        "request_id": "req-12345-abcde",
        "enqueued_at": "2026-10-29T02:00:00Z",
        # Sensitive or unapproved payload fields
        "application_text": "John Doe confidential resume text",
        "candidate_email": "john.doe@example.com",
        "raw_prompt": "System prompt with internal instructions",
        "some_numeric_key": 9999,
    }

    headers = build_task_headers(
        payload, tenant_id="tenant-alpha", event_id="event-uuid-123"
    )

    # Mandatory fields present
    assert headers["tenant_id"] == "tenant-alpha"
    assert headers["outbox_event_id"] == "event-uuid-123"

    # Approved correlation keys present
    assert (
        headers["traceparent"]
        == "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    )
    assert headers["request_id"] == "req-12345-abcde"
    assert headers["enqueued_at"] == "2026-10-29T02:00:00Z"

    # Arbitrary keys excluded
    assert "application_text" not in headers
    assert "candidate_email" not in headers
    assert "raw_prompt" not in headers
    assert "some_numeric_key" not in headers


def test_build_task_headers_sanitization_and_bounds():
    """Verify tenant_id truncation, oversized value exclusion, and non-string filter."""
    oversized_value = "x" * 300
    payload = {
        "traceparent": oversized_value,  # Exceeds 256 chars limit
        "request_id": "",  # Empty string
        "enqueued_at": 1700000000,  # Non-string
    }

    long_tenant_id = "tenant-" + "a" * 200
    long_event_id = "event-" + "b" * 200

    headers = build_task_headers(
        payload, tenant_id=long_tenant_id, event_id=long_event_id
    )

    # Length bounded to 128 chars for tenant_id and event_id
    assert len(headers["tenant_id"]) == 128
    assert headers["tenant_id"].startswith("tenant-")
    assert len(headers["outbox_event_id"]) == 128
    assert headers["outbox_event_id"].startswith("event-")

    # Invalid/oversized/empty fields excluded
    assert "traceparent" not in headers
    assert "request_id" not in headers
    assert "enqueued_at" not in headers
