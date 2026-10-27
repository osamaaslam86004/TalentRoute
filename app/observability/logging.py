"""Process-wide logging for the API, Celery workers and CLI scripts.

* ``console`` (default) keeps the historical one-line format; correlation fields are appended only
  when something is bound, so output is unchanged until code starts binding context.
* ``json`` emits one object per line with service metadata, correlation ids and trace ids.

Neither format ever serialises arbitrary ``extra=`` fields, only the allow-list in ``policy``. In
staging/production the JSON formatter also replaces exception text with the exception *type* and a
frame summary, because provider and driver errors can echo request or applicant content.

Standard-library ``logging`` is kept on purpose: every existing ``logging.getLogger(__name__)`` call
site works unchanged.
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
import traceback
from datetime import UTC, datetime
from functools import lru_cache
from importlib import metadata
from typing import IO, Any

from app.core.config import Settings, get_settings
from app.observability.context import CONTEXT_FIELDS, context_fields
from app.observability.policy import (
    ALLOWED_LOG_EXTRA_FIELDS,
    exception_type_name,
    stable_error_code,
)

try:  # opentelemetry-api is a declared dependency, but logging must work without it.
    from opentelemetry import trace as _otel_trace
except ImportError:  # pragma: no cover
    _otel_trace = None

CONSOLE_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"

_HANDLER_MARK = "_observability_handler"
_CORRELATION_FIELDS: tuple[str, ...] = (*CONTEXT_FIELDS, "trace_id", "span_id")
_PACKAGE_NAME = "langgraph-candidate-screening-service"
_MAX_EXTRA_VALUE = 256
_MAX_FRAMES = 25
_SCRUBBED_ENVIRONMENTS = frozenset({"staging", "production"})


@lru_cache(maxsize=1)
def _package_version() -> str:
    try:
        return metadata.version(_PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        return "unknown"


class ContextFilter(logging.Filter):
    """Copy the bound correlation context and the active trace ids onto each record.

    Attached to the *handler*, not a logger: logger-level filters are skipped for records that
    propagate up from child loggers, which is nearly every record in this codebase.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        bound = context_fields()
        for name in CONTEXT_FIELDS:
            # Overwrite unconditionally so ``extra={"tenant_id": ...}`` cannot spoof correlation.
            setattr(record, name, bound.get(name))

        record.trace_id = None
        record.span_id = None
        if _otel_trace is not None:
            span_context = _otel_trace.get_current_span().get_span_context()
            if span_context.is_valid:
                record.trace_id = format(span_context.trace_id, "032x")
                record.span_id = format(span_context.span_id, "016x")
        return True


def _bounded(value: Any) -> Any:
    if isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return str(value)[:_MAX_EXTRA_VALUE]


def _scrubbed_exception(exc_info: tuple[Any, Any, Any]) -> dict[str, Any]:
    """Type, optional provider code and a frame summary. Never the message or locals."""

    exc_type, exc, tb = exc_info
    target = exc if exc is not None else exc_type
    info: dict[str, Any] = {"exc_type": exception_type_name(target)}

    if exc is not None:
        code = stable_error_code(exc)
        if code:
            info["exc_code"] = code
        if exc.__cause__ is not None:
            info["exc_cause_type"] = exception_type_name(exc.__cause__)

    frames = [
        f"{os.path.basename(frame.f_code.co_filename)}:{lineno}:{frame.f_code.co_name}"
        for frame, lineno in traceback.walk_tb(tb)
    ]
    if frames:
        info["exc_frames"] = frames[-_MAX_FRAMES:]
    return info


class JsonFormatter(logging.Formatter):
    def __init__(
        self,
        *,
        service: str,
        version: str,
        environment: str,
        role: str,
        scrub_exceptions: bool,
    ) -> None:
        super().__init__()
        self._static = {
            "service": service,
            "version": version,
            "environment": environment,
            "role": role,
        }
        self._scrub_exceptions = scrub_exceptions

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, UTC).isoformat(
            timespec="milliseconds"
        )
        payload: dict[str, Any] = {
            "timestamp": timestamp.replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **self._static,
        }

        for name in _CORRELATION_FIELDS:
            value = getattr(record, name, None)
            if value:
                payload[name] = value

        for name in sorted(ALLOWED_LOG_EXTRA_FIELDS):
            value = record.__dict__.get(name)
            if value is not None:
                bounded = _bounded(value)
                if bounded is not None:
                    payload[name] = bounded

        if record.exc_info and record.exc_info[0] is not None:
            if self._scrub_exceptions:
                payload.update(_scrubbed_exception(record.exc_info))
            else:
                payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info and not self._scrub_exceptions:
            payload["stack"] = self.formatStack(record.stack_info)

        return json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), default=str
        )


class ConsoleFormatter(logging.Formatter):
    """The historical format, plus ``key=value`` correlation fields when any are bound."""

    def __init__(self) -> None:
        super().__init__(CONSOLE_FORMAT)

    def format(self, record: logging.LogRecord) -> str:
        suffix = " ".join(
            f"{name}={value}"
            for name in _CORRELATION_FIELDS
            if (value := getattr(record, name, None))
        )
        if not suffix:
            return super().format(record)

        # Insert on the message line, ahead of any traceback that super().format() appends.
        original_msg, original_args = record.msg, record.args
        record.msg, record.args = f"{record.getMessage()} [{suffix}]", None
        try:
            return super().format(record)
        finally:
            record.msg, record.args = original_msg, original_args


def _resolve_level(name: str) -> int:
    level = getattr(logging, str(name).upper(), None)
    return level if isinstance(level, int) else logging.INFO


def _build_formatter(settings: Settings, role: str) -> logging.Formatter:
    if settings.log_format == "json":
        return JsonFormatter(
            service=settings.service_name,
            version=settings.service_version or _package_version(),
            environment=settings.environment,
            role=role,
            scrub_exceptions=settings.environment in _SCRUBBED_ENVIRONMENTS,
        )
    return ConsoleFormatter()


def _route_uvicorn_through_root() -> None:
    """Uvicorn installs its own handlers and sets ``propagate=False`` before the app is imported.

    Removing them sends its lifecycle and access lines through the same formatter as ours, so a
    log pipeline sees one format. Levels are left untouched, so ``--log-level`` keeps working.
    """

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        framework_logger = logging.getLogger(name)
        framework_logger.handlers.clear()
        framework_logger.propagate = True


def configure_logging(
    settings: Settings | None = None,
    *,
    role: str = "api",
    stream: IO[str] | None = None,
) -> logging.Handler:
    """Install the root handler. Idempotent; foreign handlers (pytest, Celery) are left alone."""

    settings = settings or get_settings()
    root = logging.getLogger()

    for existing in list(root.handlers):
        if getattr(existing, _HANDLER_MARK, False):
            root.removeHandler(existing)
            existing.close()

    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.addFilter(ContextFilter())
    handler.setFormatter(_build_formatter(settings, role))
    setattr(handler, _HANDLER_MARK, True)

    root.addHandler(handler)
    root.setLevel(_resolve_level(settings.log_level))

    # Only in JSON mode: console mode must keep uvicorn's own familiar output exactly as before.
    if role == "api" and settings.log_format == "json":
        _route_uvicorn_through_root()

    if (
        settings.log_format == "console"
        and settings.environment in _SCRUBBED_ENVIRONMENTS
    ):
        logging.getLogger(__name__).warning(
            "LOG_FORMAT=console in %s: exception messages are not scrubbed; use LOG_FORMAT=json",
            settings.environment,
        )
    return handler
