import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.rate_limit import close_rate_limit_client
from app.db.session import engine
from app.observability import setup_observability
from app.observability.instrumentation import instrument_api, uninstrument_api
from app.observability.metrics import (
    flush_metrics,
    initialize_metrics,
    shutdown_metrics,
)
from app.observability.middleware import RequestTelemetryMiddleware
from app.observability.tracing import (
    flush_tracing,
    initialize_tracing,
    shutdown_tracing,
)

settings = get_settings()
setup_observability("api", settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.otel_enabled:
        initialize_tracing(settings)
        initialize_metrics(settings)
    try:
        yield
    finally:
        try:
            await close_rate_limit_client()
        finally:
            try:
                await engine.dispose()
            finally:
                uninstrument_api(app, settings)
                flush_metrics()
                flush_tracing()
                shutdown_metrics()
                shutdown_tracing()


app = FastAPI(
    title=settings.app_name,
    version="1.2.0",
    description=(
        "Evidence-focused candidate-screening decision support. Outputs are advisory and require "
        "human review; the service does not make or automate hiring decisions."
    ),
    lifespan=lifespan,
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url=None,
    openapi_url=None if settings.environment == "production" else "/openapi.json",
)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "X-API-Key",
            "Authorization",
            "X-Request-ID",
            "Idempotency-Key",
        ],
        expose_headers=["X-Request-ID"],
    )

app.add_middleware(RequestTelemetryMiddleware)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Avoid reflecting exception text, prompts, or applicant data in client-visible errors.
    import logging

    logging.getLogger(__name__).error(
        "unhandled request error request_id=%s exception_type=%s",
        getattr(request.state, "request_id", "unknown"),
        type(exc).__name__,
    )
    request_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=500,
        headers={
            "X-Request-ID": request_id or "unknown",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
        },
        content={
            "detail": "Internal server error",
            "request_id": request_id,
        },
    )


app.include_router(api_router)
instrument_api(app, engine, settings)
