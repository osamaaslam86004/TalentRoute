"""Low-cardinality spans and helpers for the asynchronous screening pipeline."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.trace import Span

_ALLOWED_STAGES = frozenset(
    {"claim", "redact", "graph", "persist", "outbox.dispatch", "outbox.batch"}
)
_TRACER = trace.get_tracer("app.screening.pipeline")


@contextmanager
def traced_stage(stage: str, *, screening_id: str | None = None) -> Iterator[Span]:
    """Create a stage span without recording application text or any caller-supplied content."""

    safe_stage = stage if stage in _ALLOWED_STAGES else "unknown"
    attributes: dict[str, str] = {"app.stage": safe_stage}

    if screening_id and len(screening_id) <= 128:
        attributes["app.screening_id"] = screening_id

    with _TRACER.start_as_current_span(
        f"screening.{safe_stage}", attributes=attributes
    ) as span:
        yield span
