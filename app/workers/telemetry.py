"""Pure helpers for carrying approved telemetry metadata through Celery headers."""

from __future__ import annotations

from collections.abc import Mapping


def build_task_headers(
    payload: Mapping[str, object], *, tenant_id: str, event_id: str
) -> dict[str, str]:
    """Copy only approved correlation metadata; arbitrary outbox payload keys are never headers."""

    headers: dict[str, str] = {
        "tenant_id": str(tenant_id)[:128],
        "outbox_event_id": str(event_id)[:128],
    }

    for key in ("traceparent", "request_id", "enqueued_at"):
        value = payload.get(key)
        if isinstance(value, str) and value and len(value) <= 256:
            headers[key] = value

    return headers
