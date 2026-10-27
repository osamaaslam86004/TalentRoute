from __future__ import annotations

import uuid
from typing import Any

import pytest
from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter
from opentelemetry.trace import Status, StatusCode

from app.observability.middleware import RequestTelemetryMiddleware
from app.observability.tracing import SanitizingSpanExporter


class _Route:
    path = "/api/v1/screenings/{screening_id}"


class _MemoryExporter(SpanExporter):
    def __init__(self) -> None:
        self.spans: list[ReadableSpan] = []

    def export(self, spans) -> Any:
        from opentelemetry.sdk.trace.export import SpanExportResult

        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


@pytest.mark.asyncio
async def test_middleware_sets_correlation_and_security_headers(monkeypatch):
    recorded: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "app.observability.middleware.record_http_request",
        lambda **kwargs: recorded.append(kwargs),
    )
    sent: list[dict[str, Any]] = []

    async def downstream(scope, receive, send):
        await send({"type": "http.response.start", "status": 201, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/screenings/not-a-metric-label",
        "headers": [(b"x-request-id", b"not-a-uuid")],
        "route": _Route(),
        "state": {},
    }

    async def send(message):
        sent.append(message)

    await RequestTelemetryMiddleware(downstream)(scope, receive, send)

    start = next(
        message for message in sent if message["type"] == "http.response.start"
    )
    headers = dict(start["headers"])
    request_id = headers[b"x-request-id"].decode("ascii")
    assert str(uuid.UUID(request_id)) == request_id
    assert headers[b"x-content-type-options"] == b"nosniff"
    assert headers[b"x-frame-options"] == b"DENY"
    assert headers[b"referrer-policy"] == b"no-referrer"
    assert scope["state"]["request_id"] == request_id
    assert recorded[0]["route"] == "/api/v1/screenings/{screening_id}"
    assert recorded[0]["method"] == "POST"
    assert recorded[0]["status_code"] == 201
    assert recorded[0]["duration_seconds"] >= 0
    assert "not-a-metric-label" not in repr(recorded)


@pytest.mark.asyncio
async def test_middleware_records_unhandled_exception_as_server_error(monkeypatch):
    recorded: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "app.observability.middleware.record_http_request",
        lambda **kwargs: recorded.append(kwargs),
    )

    async def downstream(scope, receive, send):
        raise RuntimeError("private applicant content must not be telemetry")

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {"type": "http", "method": "GET", "path": "/failure", "headers": []}
    with pytest.raises(RuntimeError):
        await RequestTelemetryMiddleware(downstream)(scope, receive, _noop)
    assert recorded[0]["status_code"] == 500
    assert recorded[0]["route"] == "unmatched"


async def _noop(message):
    return None


def test_span_exporter_removes_raw_urls_sql_and_exception_messages():
    target = _MemoryExporter()
    exporter = SanitizingSpanExporter(target)
    span = ReadableSpan(
        name="GET https://internal.example/path?email=jane@example.com",
        attributes={
            "http.route": "/api/v1/screenings/{screening_id}",
            "http.request.method": "GET",
            "http.url": "https://internal.example/?email=jane@example.com",
            "db.statement": "SELECT * FROM screening WHERE application_text='PRIVATE'",
            "app.stage": "persistence",
        },
        events=[Event("exception", attributes={"exception.message": "PRIVATE"})],
        status=Status(StatusCode.ERROR, description="PRIVATE applicant payload"),
    )
    exporter.export([span])

    (exported,) = target.spans
    assert exported.name == "GET /api/v1/screenings/{screening_id}"
    assert exported.attributes == {
        "http.route": "/api/v1/screenings/{screening_id}",
        "http.request.method": "GET",
        "app.stage": "persistence",
    }
    assert exported.events == ()
    assert exported.status.description is None
    assert "jane@example.com" not in repr(exported.attributes)
    assert "PRIVATE" not in repr(exported)
