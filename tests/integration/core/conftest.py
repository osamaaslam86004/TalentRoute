"""Shared pytest fixtures for app/core integration and security tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from typing import Any

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.core.config import Settings
from app.core.security import Principal, get_principal, require_permission


@pytest.fixture
def base_settings_kwargs() -> dict[str, Any]:
    """Provide valid default kwargs for Settings instantiation."""
    return {
        "app_name": "Test Candidate Screening API",
        "environment": "test",
        "auth_mode": "auto",
        "api_key": SecretStr("test-secret-api-key-12345"),
        "oidc_issuer": "https://auth.example.com/realms/test",
        "oidc_audience": "candidate-screening-api",
        "oidc_jwks_url": "https://auth.example.com/realms/test/.well-known/jwks.json",
        "redis_url": "redis://localhost:6379/0",
    }


@pytest.fixture
def mock_settings(base_settings_kwargs: dict[str, Any]) -> Settings:
    """Return a fully configured Settings instance for testing."""
    return Settings(**base_settings_kwargs)


@pytest.fixture
def principal_factory() -> Callable[..., Principal]:
    """Factory fixture to create Principal instances for testing RBAC rules."""

    def _create_principal(
        subject: str = "test-user-001",
        tenant_id: str = "tenant-alpha",
        roles: list[str] | None = None,
        scopes: list[str] | None = None,
        auth_method: str = "oidc",
    ) -> Principal:
        return Principal(
            subject=subject,
            tenant_id=tenant_id,
            roles=frozenset(roles or []),
            scopes=frozenset(scopes or []),
            auth_method=auth_method,
        )

    return _create_principal


@pytest.fixture
def core_test_app() -> FastAPI:
    """FastAPI application with endpoints for testing security dependencies."""
    app = FastAPI(title="Core Security Test App")

    @app.get("/public/health")
    async def public_health():
        return {"status": "ok"}

    @app.get("/api/v1/user/me")
    async def get_me(principal: Principal = Depends(get_principal)):
        return {
            "subject": principal.subject,
            "tenant_id": principal.tenant_id,
            "roles": list(principal.roles),
            "scopes": list(principal.scopes),
            "auth_method": principal.auth_method,
        }

    @app.post("/api/v1/screenings")
    async def create_screening(
        principal: Principal = Depends(require_permission("screening:write")),
    ):
        return {"status": "screening_created", "tenant_id": principal.tenant_id}

    @app.get("/api/v1/screenings")
    async def list_screenings(
        principal: Principal = Depends(require_permission("screening:read")),
    ):
        return {"status": "screenings_listed", "tenant_id": principal.tenant_id}

    @app.delete("/api/v1/screenings/{screening_id}")
    async def delete_screening(
        screening_id: str,
        principal: Principal = Depends(require_permission("screening:delete")),
    ):
        return {
            "status": "screening_deleted",
            "screening_id": screening_id,
            "tenant_id": principal.tenant_id,
        }

    @app.get("/api/v1/audit-events")
    async def list_audit(
        principal: Principal = Depends(require_permission("audit:read")),
    ):
        return {"status": "audit_read", "tenant_id": principal.tenant_id}

    return app


@pytest_asyncio.fixture
async def async_core_client(
    core_test_app: FastAPI,
) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client bound to the test FastAPI app."""
    transport = ASGITransport(app=core_test_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
