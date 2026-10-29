"""Global pytest fixtures for authentication and security testing."""

from collections.abc import AsyncGenerator, Callable
from typing import Any
from unittest.mock import MagicMock

from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio

from app.core.config import Settings
from app.core.security import Principal, get_principal, require_permission


@pytest.fixture
def base_settings_kwargs() -> dict[str, Any]:
    return {
        "app_name": "Test Screening API",
        "environment": "test",
        "auth_mode": "auto",
        "oidc_issuer": "https://auth.example.com/",
        "oidc_audience": "candidate-screening-api",
        "oidc_jwks_url": "https://auth.example.com/.well-known/jwks.json",
        "oidc_roles_claim": "roles",
        "oidc_scopes_claim": "scope",
        "oidc_tenant_claim": "tenant_id",
        "oidc_require_tenant_claim": True,
        "oidc_algorithms": ["RS256"],
    }


@pytest.fixture
def mock_settings(base_settings_kwargs: dict[str, Any]) -> Settings:
    return Settings(**base_settings_kwargs)


@pytest.fixture
def make_principal() -> Callable[..., Principal]:
    def _factory(
        subject: str = "user-123",
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

    return _factory


@pytest.fixture
def sample_jwt_claims() -> dict[str, Any]:
    return {
        "sub": "user-456",
        "iss": "https://auth.example.com/",
        "aud": "candidate-screening-api",
        "exp": 4102444800,  # Far future
        "iat": 1600000000,
        "tenant_id": "tenant-alpha",
        "roles": ["recruiter"],
        "scope": "screening:write screening:read",
    }


@pytest.fixture
def mock_jwk_key() -> MagicMock:
    key_mock = MagicMock()
    key_mock.key = "mock_public_key_pem"
    return key_mock


@pytest.fixture
def mock_jwks_client(mock_jwk_key: MagicMock) -> MagicMock:
    client_mock = MagicMock()
    client_mock.get_signing_key_from_jwt.return_value = mock_jwk_key
    return client_mock


@pytest.fixture
def test_app() -> FastAPI:
    app = FastAPI(title="Test Security API")

    @app.get("/public")
    async def public_endpoint() -> dict[str, str]:
        return {"status": "public"}

    @app.get("/me")
    async def me_endpoint(
        principal: Principal = Depends(get_principal),
    ) -> dict[str, Any]:
        return {
            "subject": principal.subject,
            "tenant_id": principal.tenant_id,
            "roles": sorted(principal.roles),
            "scopes": sorted(principal.scopes),
            "auth_method": principal.auth_method,
        }

    @app.get("/screenings/write")
    async def protected_screening_write(
        principal: Principal = Depends(require_permission("screening:write")),
    ) -> dict[str, str]:
        return {"status": "write_granted", "subject": principal.subject}

    @app.get("/screenings/read")
    async def protected_screening_read(
        principal: Principal = Depends(require_permission("screening:read")),
    ) -> dict[str, str]:
        return {"status": "read_granted", "subject": principal.subject}

    @app.delete("/screenings/{screening_id}")
    async def protected_screening_delete(
        screening_id: str,
        principal: Principal = Depends(require_permission("screening:delete")),
    ) -> dict[str, str]:
        return {"status": "deleted", "screening_id": screening_id}

    @app.get("/reviews/appeal")
    async def protected_review_appeal(
        principal: Principal = Depends(require_permission("review:appeal")),
    ) -> dict[str, str]:
        return {"status": "appeal_granted", "subject": principal.subject}

    @app.get("/audit/read")
    async def protected_audit_read(
        principal: Principal = Depends(require_permission("audit:read")),
    ) -> dict[str, str]:
        return {"status": "audit_granted", "subject": principal.subject}

    return app


@pytest_asyncio.fixture
async def async_client(test_app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=test_app)
    async with AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        yield client
