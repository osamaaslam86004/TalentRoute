"""Vendor-neutral RED metrics with strict low-cardinality labels and OTLP export."""

from __future__ import annotations

import logging
from threading import Lock
from typing import Any

from opentelemetry import metrics

from app.core.config import Settings
from app.observability.policy import sanitize_metric_labels

logger = logging.getLogger(__name__)
_lock = Lock()
_provider: Any | None = None
_request_counter: Any | None = None
_request_duration: Any | None = None
_auth_failure_counter: Any | None = None
_rate_limit_counter: Any | None = None
_pipeline_events: Any | None = None
_screening_duration: Any | None = None
_queue_wait_duration: Any | None = None
_end_to_end_duration: Any | None = None
_outbox_dispatch_lag: Any | None = None
_ALLOWED_AUTH_REASONS = frozenset(
    {"missing", "invalid", "forbidden", "jwks_unavailable"}
)
_ALLOWED_RATE_DECISIONS = frozenset(
    {"allowed", "limited", "backend_error_closed", "backend_error_open"}
)
_PIPELINE_EVENT_TYPES = frozenset(
    {
        "outbox.claimed",
        "outbox.dispatched",
        "outbox.failed",
        "outbox.dead",
        "celery.task.started",
        "celery.task.completed",
        "celery.task.retry",
        "celery.task.failed",
        "screening.duplicate",
        "screening.reclaimed",
        "screening.completed",
        "screening.failed",
        "pipeline.other",
    }
)
_PIPELINE_OUTCOMES = frozenset(
    {"success", "failure", "retry", "dead", "duplicate", "reclaimed", "unknown"}
)
_PIPELINE_STAGES = frozenset(
    {"outbox", "celery", "claim", "redact", "graph", "persist", "unknown"}
)


def _signal_endpoint(settings: Settings, signal: str) -> str:
    endpoint = settings.otel_exporter_otlp_endpoint.rstrip("/")
    if endpoint.endswith(f"/v1/{signal}"):
        return endpoint
    if endpoint.endswith("/v1"):
        return f"{endpoint}/{signal}"
    return f"{endpoint}/v1/{signal}"


def initialize_metrics(settings: Settings) -> None:
    """Create an asynchronous OTLP metric reader once per process when telemetry is enabled."""
    global _provider, _request_counter, _request_duration, _auth_failure_counter, _rate_limit_counter
    global _pipeline_events, _screening_duration, _queue_wait_duration
    global _end_to_end_duration, _outbox_dispatch_lag
    if not settings.otel_enabled:
        return
    with _lock:
        if _provider is not None:
            return
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create(
            {
                "service.name": settings.service_name,
                "service.version": settings.service_version or "unknown",
                "deployment.environment.name": settings.environment,
            }
        )
        exporter = OTLPMetricExporter(
            endpoint=_signal_endpoint(settings, "metrics"), timeout=5
        )
        reader = PeriodicExportingMetricReader(
            exporter, export_interval_millis=5000, export_timeout_millis=5000
        )
        provider = MeterProvider(resource=resource, metric_readers=[reader])
        metrics.set_meter_provider(provider)
        meter = metrics.get_meter("app.observability", "1.0.0")
        _request_counter = meter.create_counter(
            "app.http.server.request.count",
            unit="{request}",
            description="Completed HTTP requests",
        )
        _request_duration = meter.create_histogram(
            "app.http.server.request.duration",
            unit="s",
            description="HTTP request duration",
        )
        _auth_failure_counter = meter.create_counter(
            "auth.failures",
            unit="{failure}",
            description="Authentication and authorization failures",
        )
        _rate_limit_counter = meter.create_counter(
            "screening.rate_limit.decisions",
            unit="{decision}",
            description="Screening rate-limit decisions",
        )
        _pipeline_events = meter.create_counter(
            "app.pipeline.events",
            unit="{event}",
            description="Outbox and screening worker lifecycle events",
        )
        _screening_duration = meter.create_histogram(
            "app.screening.processing.duration",
            unit="s",
            description="Screening task execution time",
        )
        _queue_wait_duration = meter.create_histogram(
            "app.pipeline.queue.wait",
            unit="s",
            description="Time from outbox enqueue to worker start",
        )
        _end_to_end_duration = meter.create_histogram(
            "app.screening.end_to_end.duration",
            unit="s",
            description="Time from screening enqueue to processing completion",
        )
        _outbox_dispatch_lag = meter.create_histogram(
            "app.outbox.dispatch.lag",
            unit="s",
            description="Time from outbox enqueue to broker dispatch",
        )
        _provider = provider


def _safe_add(instrument: Any | None, value: int, attributes: dict[str, str]) -> None:
    if instrument is None:
        return
    try:
        instrument.add(value, attributes)
    except Exception as exc:  # telemetry must not affect application behavior
        logger.debug("metric update failed error_type=%s", type(exc).__name__)


def record_http_request(
    *, route: str, method: str, status_code: int, duration_seconds: float
) -> None:
    if _request_counter is None:
        return
    status_class = f"{max(0, min(5, int(status_code) // 100))}xx"
    attributes = sanitize_metric_labels(
        {"route": route, "method": method, "status_class": status_class}
    )
    _safe_add(_request_counter, 1, attributes)
    try:
        _request_duration.record(max(0.0, float(duration_seconds)), attributes)
    except Exception as exc:
        logger.debug("metric observation failed error_type=%s", type(exc).__name__)


def record_auth_failure(reason: str) -> None:
    normalized = reason if reason in _ALLOWED_AUTH_REASONS else "invalid"
    attributes = sanitize_metric_labels({"reason": normalized})
    _safe_add(_auth_failure_counter, 1, attributes)


def record_rate_limit_decision(decision: str) -> None:
    normalized = (
        decision if decision in _ALLOWED_RATE_DECISIONS else "backend_error_closed"
    )
    attributes = sanitize_metric_labels({"decision": normalized})
    _safe_add(_rate_limit_counter, 1, attributes)


def record_pipeline_event(
    event_type: str,
    *,
    outcome: str,
    stage: str,
    error_type: str | None = None,
    status: str | None = None,
    value: int = 1,
) -> None:
    """Record a pipeline event using fixed vocabularies and low-cardinality labels only."""
    normalized_event = (
        event_type if event_type in _PIPELINE_EVENT_TYPES else "pipeline.other"
    )
    normalized_outcome = outcome if outcome in _PIPELINE_OUTCOMES else "unknown"
    normalized_stage = stage if stage in _PIPELINE_STAGES else "unknown"
    attributes = sanitize_metric_labels(
        {
            "event_type": normalized_event,
            "outcome": normalized_outcome,
            "stage": normalized_stage,
            "error_type": error_type,
            "status": status,
        }
    )
    _safe_add(_pipeline_events, max(0, int(value)), attributes)


def _record_duration(
    instrument: Any | None,
    seconds: float,
    attributes: dict[str, str] | None = None,
) -> None:
    if instrument is None:
        return
    try:
        value = float(seconds)
        if value < 0 or value != value or value == float("inf"):
            return
        instrument.record(value, attributes or {})
    except Exception as exc:  # telemetry must never change task behavior
        logger.debug("duration metric failed error_type=%s", type(exc).__name__)


def record_screening_duration(seconds: float, outcome: str) -> None:
    normalized = (
        outcome if outcome in {"success", "failure", "retry", "unknown"} else "unknown"
    )
    _record_duration(
        _screening_duration, seconds, sanitize_metric_labels({"outcome": normalized})
    )


def record_queue_wait(seconds: float) -> None:
    _record_duration(_queue_wait_duration, seconds)


def record_end_to_end_duration(seconds: float) -> None:
    _record_duration(_end_to_end_duration, seconds)


def record_outbox_dispatch_lag(seconds: float) -> None:
    _record_duration(_outbox_dispatch_lag, seconds)


def flush_metrics(timeout_millis: int = 5000) -> bool:
    provider = _provider
    if provider is None:
        return True
    try:
        return bool(provider.force_flush(timeout_millis=timeout_millis))
    except Exception as exc:
        logger.warning("metric flush failed error_type=%s", type(exc).__name__)
        return False


def shutdown_metrics() -> None:
    global _provider, _request_counter, _request_duration, _auth_failure_counter, _rate_limit_counter
    global _pipeline_events, _screening_duration, _queue_wait_duration
    global _end_to_end_duration, _outbox_dispatch_lag
    with _lock:
        provider = _provider
        _provider = None
        _request_counter = _request_duration = None
        _auth_failure_counter = _rate_limit_counter = None
        _pipeline_events = _screening_duration = _queue_wait_duration = None
        _end_to_end_duration = _outbox_dispatch_lag = None
    if provider is not None:
        try:
            provider.shutdown(timeout_millis=5000)
        except Exception as exc:
            logger.warning("metric shutdown failed error_type=%s", type(exc).__name__)
