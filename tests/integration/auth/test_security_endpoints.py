"""Integration security tests for get_principal and require_permission FastAPI dependencies."""

from unittest.mock import MagicMock, patch

from httpx import AsyncClient
from pydantic import SecretStr
import pytest

from app.core.config import Settings
from app.core.security import Principal


class TestSecurityDependenciesIntegration:
    """Integration test suite for authentication mechanisms and RBAC enforcement."""

    @pytest.mark.asyncio
    @patch("app.core.security.get_settings")
    @patch("app.core.security._decode_oidc_token")
    async def test_oidc_bearer_authentication_success(
        self,
        mock_decode_oidc: MagicMock,
        mock_get_settings: MagicMock,
        async_client: AsyncClient,
        mock_settings: Settings,
    ) -> None:
        mock_get_settings.return_value = mock_settings
        mock_decode_oidc.return_value = Principal(
            subject="oidc-subject-001",
            tenant_id="tenant-alpha",
            roles=frozenset({"recruiter"}),
            scopes=frozenset({"screening:write"}),
            auth_method="oidc",
        )

        headers = {"Authorization": "Bearer valid.oidc.token"}
        response = await async_client.get("/me", headers=headers)

        assert response.status_code == 200
        data = response.json()
        assert data["subject"] == "oidc-subject-001"
        assert data["tenant_id"] == "tenant-alpha"
        assert data["auth_method"] == "oidc"

    @pytest.mark.asyncio
    @patch("app.core.security.get_settings")
    async def test_oidc_strict_mode_missing_credentials_returns_401(
        self,
        mock_get_settings: MagicMock,
        async_client: AsyncClient,
        base_settings_kwargs: dict[str, object],
    ) -> None:
        kwargs = dict(base_settings_kwargs)
        kwargs["auth_mode"] = "oidc"
        mock_get_settings.return_value = Settings(**kwargs)

        response = await async_client.get("/me")
        assert response.status_code == 401
        assert response.headers.get("WWW-Authenticate") == "Bearer"

    @pytest.mark.asyncio
    @patch("app.core.security.get_settings")
    async def test_api_key_authentication_success(
        self,
        mock_get_settings: MagicMock,
        async_client: AsyncClient,
        base_settings_kwargs: dict[str, object],
    ) -> None:
        kwargs = dict(base_settings_kwargs)
        kwargs["auth_mode"] = "auto"
        kwargs["api_key"] = SecretStr("secret-api-key-12345")
        mock_get_settings.return_value = Settings(**kwargs)

        headers = {"X-API-Key": "secret-api-key-12345"}
        response = await async_client.get("/me", headers=headers)

        assert response.status_code == 200
        data = response.json()
        assert data["subject"] == "api-key-client"
        assert data["auth_method"] == "api_key"
        assert "admin" in data["roles"]

    @pytest.mark.asyncio
    @patch("app.core.security.get_settings")
    async def test_api_key_authentication_invalid_key_returns_401(
        self,
        mock_get_settings: MagicMock,
        async_client: AsyncClient,
        base_settings_kwargs: dict[str, object],
    ) -> None:
        kwargs = dict(base_settings_kwargs)
        kwargs["auth_mode"] = "auto"
        kwargs["api_key"] = SecretStr("secret-api-key-12345")
        mock_get_settings.return_value = Settings(**kwargs)

        headers = {"X-API-Key": "wrong-api-key"}
        response = await async_client.get("/me", headers=headers)

        assert response.status_code == 401
        assert "Invalid or missing credentials" in response.json()["detail"]

    @pytest.mark.asyncio
    @patch("app.core.security.get_settings")
    async def test_development_mode_fallback_when_unauthenticated(
        self,
        mock_get_settings: MagicMock,
        async_client: AsyncClient,
        base_settings_kwargs: dict[str, object],
    ) -> None:
        kwargs = dict(base_settings_kwargs)
        kwargs["auth_mode"] = "auto"
        kwargs["api_key"] = None
        kwargs["environment"] = "development"
        mock_get_settings.return_value = Settings(**kwargs)

        response = await async_client.get("/me")

        assert response.status_code == 200
        data = response.json()
        assert data["subject"] == "local-development"
        assert data["auth_method"] == "development"

    @pytest.mark.asyncio
    @patch("app.core.security.get_settings")
    @patch("app.core.security._decode_oidc_token")
    async def test_rbac_permission_boundary_screening_write(
        self,
        mock_decode_oidc: MagicMock,
        mock_get_settings: MagicMock,
        async_client: AsyncClient,
        mock_settings: Settings,
    ) -> None:
        mock_get_settings.return_value = mock_settings

        # Authorized user (Recruiter)
        mock_decode_oidc.return_value = Principal(
            subject="recruiter-01",
            tenant_id="tenant-alpha",
            roles=frozenset({"recruiter"}),
            scopes=frozenset(),
            auth_method="oidc",
        )
        auth_headers = {"Authorization": "Bearer recruiter.jwt"}
        ok_res = await async_client.get("/screenings/write", headers=auth_headers)
        assert ok_res.status_code == 200
        assert ok_res.json()["status"] == "write_granted"

        # Unauthorized user (Auditor attempting screening:write)
        mock_decode_oidc.return_value = Principal(
            subject="auditor-01",
            tenant_id="tenant-alpha",
            roles=frozenset({"auditor"}),
            scopes=frozenset(),
            auth_method="oidc",
        )
        forbidden_res = await async_client.get(
            "/screenings/write", headers=auth_headers
        )
        assert forbidden_res.status_code == 403
        assert "Insufficient permissions" in forbidden_res.json()["detail"]

    @pytest.mark.asyncio
    @patch("app.core.security.get_settings")
    @patch("app.core.security._decode_oidc_token")
    async def test_rbac_privacy_officer_screening_delete(
        self,
        mock_decode_oidc: MagicMock,
        mock_get_settings: MagicMock,
        async_client: AsyncClient,
        mock_settings: Settings,
    ) -> None:
        mock_get_settings.return_value = mock_settings

        # Privacy officer allowed delete
        mock_decode_oidc.return_value = Principal(
            subject="privacy-officer-01",
            tenant_id="tenant-alpha",
            roles=frozenset({"privacy_officer"}),
            scopes=frozenset(),
            auth_method="oidc",
        )
        auth_headers = {"Authorization": "Bearer privacy.jwt"}
        res = await async_client.delete(
            "/screenings/sc-100200", headers=auth_headers
        )
        assert res.status_code == 200
        assert res.json()["status"] == "deleted"

        # Recruiter forbidden delete
        mock_decode_oidc.return_value = Principal(
            subject="recruiter-01",
            tenant_id="tenant-alpha",
            roles=frozenset({"recruiter"}),
            scopes=frozenset(),
            auth_method="oidc",
        )
        res_forbidden = await async_client.delete(
            "/screenings/sc-100200", headers=auth_headers
        )
        assert res_forbidden.status_code == 403
