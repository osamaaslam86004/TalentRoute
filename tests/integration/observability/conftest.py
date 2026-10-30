"""Fixtures and test setup for observability integration tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from httpx import ASGITransport

from app.core.config import Settings
from app.observability.context import bind_context, current_context
from app.observability.middleware import RequestTelemetryMiddleware


@pytest.fixture
def mock_settings() -> Settings:
    """Return test settings configured for observability testing."""
    return Settings(
        environment="development",
        service_name="screening-service-test",
        service_version="1.2.0-test",
        otel_enabled=True,
        telemetry_hash_salt="test-secret-salt-12345",
        allow_langsmith_tracing=False,
    )


@pytest.fixture
def prod_settings() -> Settings:
    """Return production settings for privacy and scrubbing policy tests."""
    return Settings(
        environment="production",
        service_name="screening-service-prod",
        service_version="1.2.0-prod",
        otel_enabled=True,
        telemetry_hash_salt="prod-secret-salt-67890",
        allow_langsmith_tracing=False,
    )


@pytest.fixture
def test_app() -> FastAPI:
    """Create a FastAPI application wrapped with RequestTelemetryMiddleware."""
    app = FastAPI(title="Observability Test App")

    @app.get("/api/v1/success")
    async def success_endpoint(request: Request) -> dict[str, str]:
        bind_context(tenant_id="tenant-123", actor_hash="actor-hash-456")
        ctx = current_context()
        request_id = getattr(request.state, "request_id", None)
        return {
            "status": "ok",
            "request_id": request_id or "missing",
            "context_request_id": ctx.request_id if ctx else "none",
            "tenant_id": ctx.tenant_id if ctx else "none",
        }

    @app.post("/api/v1/error")
    async def error_endpoint(request: Request) -> JSONResponse:
        raise ValueError("Simulated handler failure with candidate SSN: 000-11-2222")

    app.add_middleware(RequestTelemetryMiddleware)
    return app


@pytest.fixture
async def async_client(test_app: FastAPI) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Provide an HTTPX async client for the test FastAPI application."""
    transport = ASGITransport(app=test_app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        yield client
