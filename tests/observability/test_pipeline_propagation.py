from __future__ import annotations

from datetime import UTC, datetime

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import (
    NonRecordingSpan,
    SpanContext,
    TraceFlags,
    set_span_in_context,
)

from app.observability.tracing import extract_trace_context, inject_trace_context
from app.workers.telemetry import build_task_headers


def test_w3c_trace_context_round_trips_across_outbox_carrier():
    parent = SpanContext(
        trace_id=0x1234567890ABCDEF1234567890ABCDEF,
        span_id=0x1234567890ABCDEF,
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    token = otel_context.attach(set_span_in_context(NonRecordingSpan(parent)))
    try:
        carrier = inject_trace_context()
    finally:
        otel_context.detach(token)
    assert set(carrier) == {"traceparent"}
    extracted = extract_trace_context(carrier)
    child_parent = trace.get_current_span(extracted).get_span_context()
    assert child_parent.trace_id == parent.trace_id
    assert child_parent.span_id == parent.span_id


def test_outbox_forwards_only_approved_correlation_metadata():
    traceparent = "00-1234567890abcdef1234567890abcdef-1234567890abcdef-01"
    headers = build_task_headers(
        {
            "traceparent": traceparent,
            "request_id": "request-123",
            "enqueued_at": datetime.now(UTC).isoformat(),
            "application_text": "CANARY_APPLICANT_TEXT",
            "email": "private@example.com",
        },
        tenant_id="tenant-safe-id",
        event_id="event-123",
    )
    assert headers["traceparent"] == traceparent
    assert headers["request_id"] == "request-123"
    assert headers["tenant_id"] == "tenant-safe-id"
    assert "application_text" not in headers
    assert "email" not in headers
    assert "CANARY_APPLICANT_TEXT" not in repr(headers)
    assert "private@example.com" not in repr(headers)
