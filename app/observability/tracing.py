"""OTLP tracing with bounded queues and an export-time privacy scrubber."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from threading import Lock
from typing import Any

from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import Link, Status

from app.core.config import Settings
from app.observability.policy import filter_span_attributes

logger = logging.getLogger(__name__)
_lock = Lock()
_provider: Any | None = None
_SAFE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_. -]{0,127}$")
_SAFE_ROUTE = re.compile(r"^/[A-Za-z0-9_{}./:-]{1,127}$")
_SAFE_METHOD = re.compile(r"^[A-Z]{1,12}$")


def _signal_endpoint(settings: Settings, signal: str) -> str:
    endpoint = settings.otel_exporter_otlp_endpoint.rstrip("/")

    if endpoint.endswith(f"/v1/{signal}"):
        return endpoint

    if endpoint.endswith("/v1"):
        return f"{endpoint}/{signal}"
    return f"{endpoint}/v1/{signal}"


def _safe_span_name(span: ReadableSpan, attributes: dict[str, Any]) -> str:
    route = attributes.get("http.route")
    method = attributes.get(
        "http.request.method", attributes.get("http.method", "HTTP")
    )

    if isinstance(route, str) and _SAFE_ROUTE.fullmatch(route):
        method_text = str(method).upper()
        if _SAFE_METHOD.fullmatch(method_text):
            return f"{method_text} {route}"[:160]

    if _SAFE_NAME.fullmatch(span.name) and "://" not in span.name:
        return span.name
    return "external.operation"


def sanitize_span(span: ReadableSpan) -> ReadableSpan:
    """Copy a span while removing disallowed attributes, exception events and status text."""
    attributes = filter_span_attributes(dict(span.attributes or {}))
    safe_events: list[Event] = []

    for event in span.events:
        if event.name.lower() in {"exception", "error"}:
            continue
        name = event.name if _SAFE_NAME.fullmatch(event.name) else "event"
        safe_events.append(
            Event(
                name=name,
                timestamp=event.timestamp,
                attributes=filter_span_attributes(dict(event.attributes or {})),
            )
        )

    safe_links = [
        Link(
            context=link.context,
            attributes=filter_span_attributes(dict(link.attributes or {})),
        )
        for link in span.links
    ]

    return ReadableSpan(
        name=_safe_span_name(span, attributes),
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=attributes,
        events=safe_events,
        links=safe_links,
        kind=span.kind,
        instrumentation_scope=span.instrumentation_scope,
        status=Status(status_code=span.status.status_code),
        start_time=span.start_time,
        end_time=span.end_time,
    )


class SanitizingSpanExporter(SpanExporter):
    """Apply privacy policy at the final export boundary, including auto-instrumented spans."""

    def __init__(self, exporter: SpanExporter) -> None:
        self._exporter = exporter

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            sanitized = [sanitize_span(span) for span in spans]
            return self._exporter.export(sanitized)
        except Exception as exc:
            logger.warning(
                "span sanitization/export failed error_type=%s", type(exc).__name__
            )
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        self._exporter.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return bool(self._exporter.force_flush(timeout_millis=timeout_millis))


def initialize_tracing(settings: Settings) -> None:
    """Install a parent-based sampler and asynchronous OTLP exporter once per process."""

    global _provider

    if not settings.otel_enabled:
        return

    with _lock:
        if _provider is not None:
            return
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

        ratio = settings.otel_trace_sample_ratio

        if ratio is None:
            ratio = 0.1 if settings.environment in {"staging", "production"} else 1.0

        resource = Resource.create(
            {
                "service.name": settings.service_name,
                "service.version": settings.service_version or "unknown",
                "deployment.environment.name": settings.environment,
            }
        )

        exporter = SanitizingSpanExporter(
            OTLPSpanExporter(endpoint=_signal_endpoint(settings, "traces"), timeout=5)
        )

        processor = BatchSpanProcessor(
            exporter,
            max_queue_size=2048,
            schedule_delay_millis=5000,
            max_export_batch_size=512,
            export_timeout_millis=5000,
        )

        provider = TracerProvider(
            resource=resource,
            sampler=ParentBased(TraceIdRatioBased(ratio)),
            shutdown_on_exit=False,
        )
        provider.add_span_processor(processor)
        trace.set_tracer_provider(provider)
        _provider = provider


def flush_tracing(timeout_millis: int = 5000) -> bool:
    provider = _provider

    if provider is None:
        return True
    try:
        return bool(provider.force_flush(timeout_millis=timeout_millis))
    except Exception as exc:
        logger.warning("trace flush failed error_type=%s", type(exc).__name__)
        return False


def shutdown_tracing() -> None:
    global _provider

    with _lock:
        provider = _provider
        _provider = None

    if provider is not None:
        try:
            provider.shutdown()
        except Exception as exc:
            logger.warning("trace shutdown failed error_type=%s", type(exc).__name__)


def inject_trace_context(carrier: dict[str, str] | None = None) -> dict[str, str]:
    """Inject only standard W3C trace context into a small transport carrier.

    The carrier is intended for the transactional outbox and Celery headers. It must never
    contain request bodies, prompts, candidate data, credentials, or arbitrary baggage.
    """

    from opentelemetry.trace.propagation.tracecontext import (
        TraceContextTextMapPropagator,
    )

    destination = carrier if carrier is not None else {}
    TraceContextTextMapPropagator().inject(destination)

    return destination


def extract_trace_context(carrier: dict[str, str] | None = None):
    """Extract a W3C parent context from transport metadata, ignoring other fields."""
    from opentelemetry.trace.propagation.tracecontext import (
        TraceContextTextMapPropagator,
    )

    traceparent = (carrier or {}).get("traceparent")

    if not isinstance(traceparent, str) or len(traceparent) > 128:
        traceparent = None

    return TraceContextTextMapPropagator().extract(
        {"traceparent": traceparent} if traceparent else {}
    )
