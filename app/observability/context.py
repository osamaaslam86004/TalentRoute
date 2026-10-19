"""Correlation context shared by logs, spans and (later) metrics exemplars.

The context is a small *mutable* object referenced from a ``ContextVar``. The indirection is
deliberate: a value bound deep inside a request (for example the tenant, which is only known after
authentication) must be visible to code that runs *after* the handler returns, such as the access-log
line emitted by middleware. Setting a plain ``ContextVar`` inside a dependency or a worker thread
would only change a copied context and be lost.

Only a fixed set of identifier fields can be bound. Applicant text, prompts, names and similar data
have no place here, and unknown keys raise so a typo cannot silently widen what gets logged.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, fields
from typing import Any

CONTEXT_FIELDS: tuple[str, ...] = (
    "request_id",
    "tenant_id",
    "actor_hash",
    "screening_id",
    "task_id",
)

_MAX_VALUE_LENGTH = 128
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _clean(value: object) -> str | None:
    """Coerce to a bounded single-line string so a value cannot forge or flood a log line."""

    if value is None:
        return None
    text = _CONTROL_CHARS.sub("?", str(value)).strip()
    return text[:_MAX_VALUE_LENGTH] or None


@dataclass(slots=True)
class TelemetryContext:
    request_id: str | None = None
    tenant_id: str | None = None
    actor_hash: str | None = None
    screening_id: str | None = None
    task_id: str | None = None

    def update(self, **values: Any) -> None:
        unknown = set(values).difference(CONTEXT_FIELDS)
        if unknown:
            raise TypeError(f"unknown telemetry context field(s): {', '.join(sorted(unknown))}")
        for key, value in values.items():
            setattr(self, key, _clean(value))

    def as_dict(self) -> dict[str, str]:
        """Return only the fields that are set."""

        result: dict[str, str] = {}
        for field in fields(self):
            value = getattr(self, field.name)
            if value is not None:
                result[field.name] = value
        return result


_current: ContextVar[TelemetryContext | None] = ContextVar("telemetry_context", default=None)


def current_context() -> TelemetryContext | None:
    return _current.get()


def context_fields() -> dict[str, str]:
    """Snapshot of the bound fields; empty when nothing is bound."""

    context = _current.get()
    return context.as_dict() if context is not None else {}


def bind_context(**values: Any) -> TelemetryContext:
    """Bind fields on the active context, creating one if none exists.

    Mutates the *shared* object, so the change is visible to whichever scope created it.
    """

    context = _current.get()
    if context is None:
        context = TelemetryContext()
        _current.set(context)
    context.update(**values)
    return context


@contextmanager
def context_scope(**values: Any) -> Iterator[TelemetryContext]:
    """Run a block with a fresh context that inherits, then overrides, the parent's fields.

    Use at process entry points (a Celery task, a CLI command, an ASGI request). Changes made inside
    the block never leak to the parent, and the parent is restored on exit.
    """

    context = TelemetryContext()
    parent = _current.get()
    if parent is not None:
        context.update(**parent.as_dict())
    context.update(**values)
    token = _current.set(context)
    try:
        yield context
    finally:
        _current.reset(token)


def clear_context() -> None:
    """Drop any bound context. Intended for test isolation and task-boundary safety nets."""

    _current.set(None)
