"""Privacy-aware instrumentation wiring for the API process.

The export boundary in ``tracing.py`` scrubs auto-instrumented attributes/events. No request
or response body capture is enabled; SQL statements and raw URLs are removed before export.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import Settings

logger = logging.getLogger(__name__)
_INSTRUMENTED_APPS: set[int] = set()


def instrument_api(app: Any, engine: Any, settings: Settings) -> None:
    """Instrument FastAPI, SQLAlchemy, Redis and HTTPX once when OTel is enabled."""
    if not settings.otel_enabled or id(app) in _INSTRUMENTED_APPS:
        return

    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    FastAPIInstrumentor.instrument_app(app, excluded_urls=r".*/health/(live|ready)$")
    SQLAlchemyInstrumentor().instrument(
        engine=engine.sync_engine, enable_commenter=False
    )
    RedisInstrumentor().instrument()
    HTTPXClientInstrumentor().instrument()
    _INSTRUMENTED_APPS.add(id(app))
    logger.info("OpenTelemetry API instrumentation configured")


def uninstrument_api(app: Any, settings: Settings) -> None:
    """Detach API instrumentation during clean shutdown; no-op when telemetry is disabled."""
    if not settings.otel_enabled or id(app) not in _INSTRUMENTED_APPS:
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    try:
        FastAPIInstrumentor.uninstrument_app(app)
    finally:
        try:
            SQLAlchemyInstrumentor().uninstrument()
        finally:
            try:
                RedisInstrumentor().uninstrument()
            finally:
                HTTPXClientInstrumentor().uninstrument()
                _INSTRUMENTED_APPS.discard(id(app))
