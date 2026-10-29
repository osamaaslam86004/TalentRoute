"""Integration tests for app/core/security.py get_principal and require_permission dependencies."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import status
from httpx import AsyncClient
from pydantic import SecretStr

from app.core.config import Settings
from app.core.security import Principal


class TestSecurityDependenciesIntegration:
    """Integration test suite for FastAPI security dependencies across auth modes and RBAC permissions."""

    @pytest.mark.asyncio
    async def test_api_key_constant_time_authentication_success(
        self, async_core_client: AsyncClient
    ) -> None:
        """Verify X-API-Key header succeeds when matching configured secret via constant-time comparison."""
        settings = Settings(
            auth_mode="auto",
            api_key=SecretStr("super-secret-api-key-99999"),
        )

        with patch("app.core.security.get_settings", return_value=settings):
            response = await async_core_client.get(
                "/api/v1/user/me",
                headers={"X-API-Key": "super-secret-api-key-99999"},
            )

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["subject"] == "api-key-client"
            assert data["tenant_id"] == "default"
            assert data["auth_method"] == "api_key"
            assert "admin" in data["roles"]

    @pytest.mark.asyncio
    async def test_api_key_authentication_failure_invalid_key(
        self, async_core_client: AsyncClient
    ) -> None:
        """Verify invalid X-API-Key returns HTTP 401 Unauthorized."""
        settings = Settings(
            auth_mode="api_key",
            api_key=SecretStr("correct-api-key"),
        )

        with patch("app.core.security.get_settings", return_value=settings):
            response = await async_core_client.get(
                "/api/v1/user/me",
                headers={"X-API-Key": "wrong-api-key"},
            )

            assert response.status_code == status.HTTP_401_UNAUTHORIZED
            assert response.json()["detail"] == "Invalid or missing credentials"

    @pytest.mark.asyncio
    async def test_development_mode_fallback_authentication(
        self, async_core_client: AsyncClient
    ) -> None:
        """Verify unauthenticated requests in development environment return local development principal."""
        settings = Settings(
            environment="development",
            auth_mode="auto",
            api_key=None,
        )

        with patch("app.core.security.get_settings", return_value=settings):
            response = await async_core_client.get("/api/v1/user/me")

            assert response.status_code == status.HTTP_200_OK
            data = response.json()
            assert data["subject"] == "local-development"
            assert data["auth_method"] == "development"

    @pytest.mark.asyncio
    async def test_require_permission_authorized_access(
        self, async_core_client: AsyncClient
    ) -> None:
        """Verify principal with required permission accesses endpoint successfully."""
        recruiter_principal = Principal(
            subject="recruiter-01",
            tenant_id="tenant-beta",
            roles=frozenset({"recruiter"}),
            scopes=frozenset({"screening:write"}),
            auth_method="oidc",
        )

        with patch("app.core.security.get_principal", return_value=recruiter_principal):
            response = await async_core_client.post("/api/v1/screenings")

            assert response.status_code == status.HTTP_200_OK
            assert response.json() == {
                "status": "screening_created",
                "tenant_id": "tenant-beta",
            }

    @pytest.mark.asyncio
    async def test_require_permission_insufficient_permissions_raises_403(
        self, async_core_client: AsyncClient
    ) -> None:
        """Verify principal lacking required permission receives HTTP 403 Forbidden."""
        reviewer_principal = Principal(
            subject="reviewer-01",
            tenant_id="tenant-beta",
            roles=frozenset({"reviewer"}),
            scopes=frozenset({"review:write"}),
            auth_method="oidc",
        )

        with patch("app.core.security.get_principal", return_value=reviewer_principal):
            # Reviewer attempting to delete a screening (requires screening:delete)
            response = await async_core_client.delete(
                "/api/v1/screenings/screening-123"
            )

            assert response.status_code == status.HTTP_403_FORBIDDEN
            assert response.json()["detail"] == "Insufficient permissions"
