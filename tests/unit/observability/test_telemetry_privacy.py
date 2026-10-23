from __future__ import annotations

import logging

from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.trace import Status, StatusCode

from app.observability.logging import JsonFormatter
from app.observability.policy import filter_span_attributes, sanitize_metric_labels
from app.observability.tracing import sanitize_span

CANARY_TEXT = "PHASE2_PRIVATE_APPLICANT_CANARY_7f2d"
CANARY_EMAIL = "private.phase2@example.invalid"


def test_sensitive_content_is_removed_from_span_attributes_events_and_status():
    span = ReadableSpan(
        name=f"screening.graph {CANARY_EMAIL}",
        attributes={
            "app.stage": "graph",
            "application_text": CANARY_TEXT,
            "http.url": f"https://internal.invalid/?email={CANARY_EMAIL}",
            "db.statement": f"SELECT '{CANARY_TEXT}'",
        },
        events=[Event("exception", attributes={"exception.message": CANARY_TEXT})],
        status=Status(StatusCode.ERROR, description=CANARY_TEXT),
    )
    exported = sanitize_span(span)
    serialized = repr(exported)
    assert CANARY_TEXT not in serialized
    assert CANARY_EMAIL not in serialized
    assert "app.stage" in exported.attributes
    assert "application_text" not in exported.attributes
    assert exported.events == ()
    assert exported.status.description is None


def test_metric_labels_drop_tenant_and_applicant_content():
    labels = sanitize_metric_labels(
        {
            "event_type": "screening.completed",
            "outcome": "success",
            "tenant_id": "tenant-123",
            "application_text": CANARY_TEXT,
            "job_title": CANARY_EMAIL,
        }
    )
    assert labels == {"event_type": "screening.completed", "outcome": "success"}
    assert CANARY_TEXT not in repr(labels)
    assert CANARY_EMAIL not in repr(labels)


def test_span_attribute_allowlist_drops_free_text_and_raw_url():
    attributes = filter_span_attributes(
        {
            "app.stage": "graph",
            "application_text": CANARY_TEXT,
            "url.full": f"https://internal.invalid/?e={CANARY_EMAIL}",
        }
    )
    assert attributes == {"app.stage": "graph"}
    assert CANARY_TEXT not in repr(attributes)
    assert CANARY_EMAIL not in repr(attributes)


def test_sensitive_canary_is_not_serialized_from_arbitrary_log_extras():
    formatter = JsonFormatter(
        service="candidate-screening",
        version="test",
        environment="test",
        role="worker",
        scrub_exceptions=True,
    )
    record = logging.LogRecord(
        name="app.workers.tasks",
        level=logging.ERROR,
        pathname="tasks.py",
        lineno=1,
        msg="screening task failed",
        args=(),
        exc_info=None,
    )
    record.application_text = CANARY_TEXT
    record.email = CANARY_EMAIL
    record.event = "screening.processing_failed"
    serialized = formatter.format(record)
    assert CANARY_TEXT not in serialized
    assert CANARY_EMAIL not in serialized
    assert '"event":"screening.processing_failed"' in serialized
