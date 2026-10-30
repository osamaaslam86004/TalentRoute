"""Integration tests for OpenTelemetry span sanitization, exporter boundaries, and metric label policies."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import SpanContext, Status, StatusCode

from app.observability.metrics import (
    record_auth_failure,
    record_http_request,
    record_pipeline_event,
)
from app.observability.policy import (
    filter_span_attributes,
    sanitize_metric_labels,
)
from app.observability.tracing import SanitizingSpanExporter, sanitize_span


def test_filter_span_attributes_policy_enforcement() -> None:
    """Verify span attribute filter drops raw URLs, SQL statements, request bodies, and headers."""
    dirty_attributes: dict[str, Any] = {
        "http.method": "POST",
        "http.route": "/api/v1/screenings",
        "http.status_code": 200,
        "app.tenant_id": "tenant-abc-123",
        "celery.task.name": "app.workers.tasks.screen_candidate",
        # Disallowed keys and prefixes
        "http.url": "https://api.screening.internal/v1/screen?applicant_ssn=000-11-2222",
        "url.full": "https://api.screening.internal/v1/screen",
        "url.query": "applicant_ssn=000-11-2222",
        "db.statement": "SELECT * FROM applicants WHERE ssn = '000-11-2222'",
        "db.query.text": "DELETE FROM screenings",
        "exception.message": "Failed processing prompt with candidate PII",
        "messaging.message.body": "{'resume': 'John Doe resume details...'}",
        "http.request.header.authorization": "Bearer secret_token_value",
        "http.request.body": "Raw request body with candidate name",
        "db.query.parameter.p1": "sensitive_param",
    }

    filtered = filter_span_attributes(dirty_attributes)

    # Approved span keys
    assert filtered["http.method"] == "POST"
    assert filtered["http.route"] == "/api/v1/screenings"
    assert filtered["http.status_code"] == 200
    assert filtered["app.tenant_id"] == "tenant-abc-123"
    assert filtered["celery.task.name"] == "app.workers.tasks.screen_candidate"

    # Disallowed keys dropped
    assert "http.url" not in filtered
    assert "url.full" not in filtered
    assert "url.query" not in filtered
    assert "db.statement" not in filtered
    assert "db.query.text" not in filtered
    assert "exception.message" not in filtered
    assert "messaging.message.body" not in filtered
    assert "http.request.header.authorization" not in filtered
    assert "http.request.body" not in filtered
    assert "db.query.parameter.p1" not in filtered


def test_filter_span_attributes_strict_mode() -> None:
    """Verify strict=True raises ValueError on disallowed span attribute keys."""
    with pytest.raises(ValueError, match="span attribute 'http.url' is not allowed"):
        filter_span_attributes({"http.url": "[suspicious link removed]"}, strict=True)


def test_sanitize_span_removes_exception_events_and_scrubs_attributes() -> None:
    """Verify sanitize_span strips exception events, cleans span names, and sanitizes event attributes."""
    span_ctx = SpanContext(
        trace_id=0x1234567890ABCDEF1234567890ABCDEF,
        span_id=0x1234567890ABCDEF,
        is_remote=False,
    )

    dirty_span = ReadableSpan(
        name="POST /api/v1/screenings",
        context=span_ctx,
        attributes={
            "http.method": "POST",
            "http.route": "/api/v1/screenings",
            "http.url": "[suspicious link removed]",
            "app.stage": "graph",
        },
        events=[
            Event(
                name="pipeline_step",
                timestamp=1000,
                attributes={
                    "app.step": "pii_redaction",
                    "http.url": "[suspicious link removed]",
                },
            ),
            # Exception event should be completely removed by sanitize_span
            Event(
                name="exception",
                timestamp=1050,
                attributes={"exception.message": "Uncaught PII error in provider"},
            ),
        ],
        status=Status(
            status_code=StatusCode.ERROR, description="Failed due to secret error"
        ),
    )

    sanitized = sanitize_span(dirty_span)

    assert sanitized.name == "POST /api/v1/screenings"
    assert "http.url" not in sanitized.attributes
    assert sanitized.attributes["http.method"] == "POST"
    assert sanitized.attributes["app.stage"] == "graph"

    # Exception event dropped; normal event kept and sanitized
    assert len(sanitized.events) == 1
    assert sanitized.events.name == "pipeline_step"
    assert "http.url" not in sanitized.events.attributes
    assert sanitized.events.attributes["app.step"] == "pii_redaction"

    # Status description stripped
    assert sanitized.status.status_code == StatusCode.ERROR
    assert getattr(sanitized.status, "description", None) is None


def test_sanitizing_span_exporter_integration() -> None:
    """Verify SanitizingSpanExporter wraps inner exporter and sanitizes spans before delegation."""
    inner_exporter = MagicMock(spec=SpanExporter)
    inner_exporter.export.return_value = SpanExportResult.SUCCESS

    sanitizing_exporter = SanitizingSpanExporter(inner_exporter)

    span_ctx = SpanContext(trace_id=1, span_id=1, is_remote=False)
    test_span = ReadableSpan(
        name="test_span",
        context=span_ctx,
        attributes={"http.method": "GET", "db.statement": "SELECT * FROM secrets"},
    )

    result = sanitizing_exporter.export([test_span])

    assert result == SpanExportResult.SUCCESS
    inner_exporter.export.assert_called_once()
    exported_spans = inner_exporter.export.call_args

    assert len(exported_spans) == 1
    sanitized_exported_span = exported_spans
    assert "db.statement" not in sanitized_exported_span.attributes
    assert sanitized_exported_span.attributes["http.method"] == "GET"


def test_sanitize_metric_labels_low_cardinality_enforcement() -> None:
    """Verify metric label sanitization allows approved labels and drops/rejects forbidden labels."""
    labels: dict[str, Any] = {
        "route": "/api/v1/screenings",
        "method": "POST",
        "status_class": "2xx",
        "event_type": "celery.task.started",
        "outcome": "success",
        "stage": "celery",
        # High cardinality & forbidden labels below
        "tenant_id": "tenant-123",
        "screening_id": "screen-456",
        "request_id": "req-789",
        "url": "[suspicious link removed]",
        "application_text": "Candidate resume content...",
    }

    sanitized = sanitize_metric_labels(labels)

    # Approved labels kept
    assert sanitized["route"] == "/api/v1/screenings"
    assert sanitized["method"] == "POST"
    assert sanitized["status_class"] == "2xx"
    assert sanitized["event_type"] == "celery.task.started"
    assert sanitized["outcome"] == "success"
    assert sanitized["stage"] == "celery"

    # Forbidden high-cardinality labels dropped
    assert "tenant_id" not in sanitized
    assert "screening_id" not in sanitized
    assert "request_id" not in sanitized
    assert "url" not in sanitized
    assert "application_text" not in sanitized


def test_sanitize_metric_labels_strict_mode() -> None:
    """Verify strict=True raises ValueError when forbidden or unknown metric labels are passed."""
    with pytest.raises(ValueError, match="metric label 'tenant_id' is not allowed"):
        sanitize_metric_labels({"tenant_id": "tenant-123"}, strict=True)

    with pytest.raises(
        ValueError, match="metric label 'unapproved_label' is not allowed"
    ):
        sanitize_metric_labels({"unapproved_label": "value"}, strict=True)


def test_metric_recording_functions_no_raise() -> None:
    """Verify metrics recording functions sanitize labels and execute cleanly."""
    record_http_request(
        route="/api/v1/screenings",
        method="POST",
        status_code=200,
        duration_seconds=0.045,
    )

    record_pipeline_event(
        "celery.task.started",
        outcome="success",
        stage="celery",
        error_type=None,
    )

    record_auth_failure("missing")
    record_auth_failure("unrecognized_reason_should_default_to_invalid")
