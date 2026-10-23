"""Pure-ASGI request correlation and RED (rate, errors, duration) telemetry.

Only route templates and bounded, low-cardinality values reach metrics. Raw URLs,
query strings, request bodies, and authorization headers are never recorded here.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from app.observability.context import context_scope
from app.observability.metrics import record_http_request

ASGIMessage = dict[str, Any]
ASGIReceive = Callable[[], Awaitable[ASGIMessage]]
ASGISend = Callable[[ASGIMessage], Awaitable[None]]
ASGIApp = Callable[[dict[str, Any], ASGIReceive, ASGISend], Awaitable[None]]

logger = logging.getLogger("app.observability.access")
_SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
)
_STANDARD_METHODS = frozenset(
    {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "CONNECT", "TRACE"}
)


def _request_id(headers: list[tuple[bytes, bytes]]) -> str:
    supplied = next(
        (value.decode("latin-1") for key, value in headers if key.lower() == b"x-request-id"),
        None,
    )
    try:
        return str(uuid.UUID(supplied)) if supplied else str(uuid.uuid4())
    except (ValueError, AttributeError, TypeError):
        return str(uuid.uuid4())


def _route_template(scope: dict[str, Any]) -> str:
    """Return a framework route template, never the caller-supplied raw path."""
    route = scope.get("route")
    template = getattr(route, "path", None)
    if not isinstance(template, str) or not template.startswith("/"):
        return "unmatched"
    return template[:128]


def _method_label(method: object) -> str:
    value = str(method or "OTHER").upper()
    return value if value in _STANDARD_METHODS else "OTHER"


class RequestTelemetryMiddleware:
    """Establish request context and record one privacy-safe access event per HTTP request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: ASGIReceive, send: ASGISend) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = list(scope.get("headers", []))
        request_id = _request_id(headers)
        state = scope.setdefault("state", {})
        if not isinstance(state, dict):
            state = {}
            scope["state"] = state
        state["request_id"] = request_id
        method = _method_label(scope.get("method"))
        started = time.perf_counter()
        response_status = 500
        failed = False
        error_type = "unknown"

        async def send_with_headers(message: ASGIMessage) -> None:
            nonlocal response_status
            if message.get("type") == "http.response.start":
                response_status = int(message.get("status", 500))
                outgoing = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"x-request-id"
                    and key.lower() not in {name for name, _ in _SECURITY_HEADERS}
                ]
                outgoing.append((b"x-request-id", request_id.encode("ascii")))
                outgoing.extend(_SECURITY_HEADERS)
                message = {**message, "headers": outgoing}
            await send(message)

        with context_scope(request_id=request_id):
            try:
                await self.app(scope, receive, send_with_headers)
            except Exception as exc:
                failed = True
                response_status = 500
                error_type = type(exc).__name__[:120]
                raise
            finally:
                duration_seconds = max(0.0, time.perf_counter() - started)
                route = _route_template(scope)
                try:
                    record_http_request(
                        route=route,
                        method=method,
                        status_code=response_status,
                        duration_seconds=duration_seconds,
                    )
                except Exception:
                    pass
                extra: dict[str, Any] = {
                    "event": "http.request.completed",
                    "method": method,
                    "route": route,
                    "status_code": response_status,
                    "duration_ms": round(duration_seconds * 1000, 3),
                    "outcome": ("error" if failed or response_status >= 500 else "success"),
                }
                if failed:
                    extra["error_type"] = error_type
                logger.info("HTTP request completed", extra=extra)
