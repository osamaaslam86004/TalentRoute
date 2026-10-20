"""Celery process and task telemetry; worker SDKs are initialized after fork."""

from __future__ import annotations

import atexit
import threading
import time
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Any

from celery.signals import (
    beat_init,
    setup_logging as celery_setup_logging,
    task_failure,
    task_postrun,
    task_prerun,
    task_retry,
    worker_process_init,
    worker_process_shutdown,
    worker_shutdown,
)
from opentelemetry import trace

from app.core.config import get_settings
from app.observability import setup_observability
from app.observability.context import context_scope
from app.observability.metrics import (
    flush_metrics,
    initialize_metrics,
    record_end_to_end_duration,
    record_pipeline_event,
    record_queue_wait,
    record_screening_duration,
    shutdown_metrics,
)
from app.observability.tracing import (
    extract_trace_context,
    flush_tracing,
    initialize_tracing,
    shutdown_tracing,
)

_lock = threading.Lock()
_active: dict[
    str,
    tuple[AbstractContextManager, AbstractContextManager, float, datetime | None, str],
] = {}


def _get_headers(task: Any) -> dict[str, str]:
    request = getattr(task, "request", None)
    raw = getattr(request, "headers", None) or {}

    if not isinstance(raw, dict):
        return {}

    return {
        key: value
        for key, value in raw.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value or len(value) > 64:
        return None

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except ValueError:
        return None


def _setup_process(role: str) -> None:
    settings = get_settings()
    setup_observability(role, settings)

    if settings.otel_enabled:
        initialize_tracing(settings)
        initialize_metrics(settings)


@celery_setup_logging.connect(weak=False)
def configure_celery_logging(**_: Any) -> None:
    setup_observability("worker", get_settings())


@worker_process_init.connect(weak=False)
def initialize_worker_telemetry(**_: Any) -> None:
    _setup_process("worker")


@beat_init.connect(weak=False)
def initialize_beat_telemetry(**_: Any) -> None:
    _setup_process("beat")
    atexit.register(_shutdown_process)


def _shutdown_process() -> None:
    flush_metrics()
    flush_tracing()
    shutdown_metrics()
    shutdown_tracing()


@worker_process_shutdown.connect(weak=False)
def shutdown_worker_process_telemetry(**_: Any) -> None:
    _shutdown_process()


@worker_shutdown.connect(weak=False)
def shutdown_worker_telemetry(**_: Any) -> None:
    _shutdown_process()


@task_prerun.connect(weak=False)
def start_task_telemetry(
    sender: Any = None,
    task_id: str | None = None,
    args: tuple | None = None,
    kwargs: dict | None = None,
    **_: Any,
) -> None:

    if sender is None:
        return

    request_id = str(
        task_id or getattr(getattr(sender, "request", None), "id", "unknown")
    )[:128]

    headers = _get_headers(sender)
    screening_id = None

    if getattr(sender, "name", "") == "app.workers.tasks.screen_candidate":
        candidate = (args or (None,))[0] if args else (kwargs or {}).get("screening_id")

        if isinstance(candidate, str) and len(candidate) <= 128:
            screening_id = candidate

    started_at = time.perf_counter()
    enqueued_at = _parse_timestamp(headers.get("enqueued_at"))
    scope_manager = context_scope(
        request_id=headers.get("request_id") or request_id,
        tenant_id=headers.get("tenant_id"),
        screening_id=screening_id,
        task_id=request_id,
    )

    scope_manager.__enter__()
    parent_context = extract_trace_context(
        {"traceparent": headers.get("traceparent", "")}
    )

    tracer = trace.get_tracer("app.workers.celery")
    span_manager = tracer.start_as_current_span(
        "celery.task",
        context=parent_context,
        attributes={
            "celery.task.name": str(getattr(sender, "name", "unknown"))[:128],
            "celery.task.id": request_id,
            "app.stage": "celery",
            **({"app.screening_id": screening_id} if screening_id else {}),
            **(
                {"app.tenant_id": headers["tenant_id"][:128]}
                if headers.get("tenant_id")
                else {}
            ),
        },
    )

    span_manager.__enter__()
    with _lock:
        _active[request_id] = (
            scope_manager,
            span_manager,
            started_at,
            enqueued_at,
            str(getattr(sender, "name", "unknown")),
        )

    if enqueued_at is not None:
        record_queue_wait(max(0.0, (datetime.now(UTC) - enqueued_at).total_seconds()))
    record_pipeline_event("celery.task.started", outcome="success", stage="celery")


@task_retry.connect(weak=False)
def record_task_retry(sender: Any = None, request: Any = None, **_: Any) -> None:

    task_id = str(
        getattr(request, "id", None)
        or getattr(getattr(sender, "request", None), "id", "unknown")
    )[:128]

    with _lock:
        entry = _active.get(task_id)

    if entry is not None:
        trace.get_current_span().set_attribute("app.outcome", "retry")
    record_pipeline_event("celery.task.retry", outcome="retry", stage="celery")


@task_failure.connect(weak=False)
def record_task_failure(
    sender: Any = None,
    task_id: str | None = None,
    exception: BaseException | None = None,
    **_: Any,
) -> None:

    safe_type = type(exception).__name__[:64] if exception is not None else "unknown"

    with _lock:
        entry = _active.get(str(task_id or "")[:128])

    if entry is not None:
        try:
            span = trace.get_current_span()
            span.set_attribute("app.outcome", "failure")
            span.set_attribute("error.type", safe_type)
        except Exception:
            pass

    record_pipeline_event(
        "celery.task.failed",
        outcome="failure",
        stage="celery",
        error_type=safe_type,
    )


@task_postrun.connect(weak=False)
def finish_task_telemetry(
    task_id: str | None = None, state: str | None = None, **_: Any
) -> None:

    key = str(task_id or "unknown")[:128]

    with _lock:
        entry = _active.pop(key, None)

    if entry is None:
        return
    scope_manager, span_manager, started_at, enqueued_at, task_name = entry
    elapsed = max(0.0, time.perf_counter() - started_at)
    normalized = {"SUCCESS": "success", "FAILURE": "failure", "RETRY": "retry"}.get(
        str(state).upper(), "unknown"
    )

    try:
        span = trace.get_current_span()
        span.set_attribute("app.outcome", normalized)
        record_pipeline_event(
            "celery.task.completed", outcome=normalized, stage="celery"
        )
        if task_name == "app.workers.tasks.screen_candidate":
            record_screening_duration(elapsed, normalized)
            if enqueued_at is not None and normalized in {"success", "failure"}:
                record_end_to_end_duration(
                    max(0.0, (datetime.now(UTC) - enqueued_at).total_seconds())
                )
    finally:
        try:
            span_manager.__exit__(None, None, None)
        finally:
            scope_manager.__exit__(None, None, None)
