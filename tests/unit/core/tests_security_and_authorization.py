import time
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from fastapi import HTTPException

from app.core.security import (
    Principal,
    _claim_strings,
    _decode_oidc_token,
    get_principal,
    require_permission,
)

# -----------------------------------------------------------------------------
# Principal & Permission Matrix Tests
# -----------------------------------------------------------------------------


def test_principal_admin_role_bypass():
    principal = Principal(
        subject="admin-1",
        tenant_id="tenant-a",
        roles=["admin"],
        permissions=[],
    )
    assert principal.can("any:permission") is True
    assert principal.can("screening:write") is True


def test_principal_explicit_permission():
    principal = Principal(
        subject="user-1",
        tenant_id="tenant-a",
        roles=["reviewer"],
        permissions=["screening:read", "screening:write"],
    )
    assert principal.can("screening:read") is True
    assert principal.can("screening:write") is True
    assert principal.can("screening:delete") is False


def test_principal_role_mapped_permissions():
    # Test roles mapping from _PERMISSION_ROLES
    reviewer_principal = Principal(
        subject="rev-1",
        tenant_id="tenant-a",
        roles=["reviewer"],
        permissions=[],
    )
    assert reviewer_principal.can("screening:read") is True
    assert reviewer_principal.can("review:write") is True
    assert reviewer_principal.can("audit:read") is False

    auditor_principal = Principal(
        subject="aud-1",
        tenant_id="tenant-a",
        roles=["auditor"],
        permissions=[],
    )
    assert auditor_principal.can("audit:read") is True
    assert auditor_principal.can("review:write") is False


def test_principal_wildcard_bypass_for_api_key_and_dev():
    api_key_principal = Principal(
        subject="key-1",
        tenant_id="tenant-a",
        roles=["service"],
        permissions=["*"],
        auth_method="api_key",
    )
    assert api_key_principal.can("anything") is True

    dev_principal = Principal(
        subject="dev-user",
        tenant_id="local-dev",
        roles=["developer"],
        permissions=["*"],
        auth_method="development",
    )
    assert dev_principal.can("anything") is True

    # OIDC token attempting wildcard injection should NOT be allowed unless explicitly handled
    oidc_wildcard_principal = Principal(
        subject="user-oidc",
        tenant_id="tenant-a",
        roles=["user"],
        permissions=["*"],
        auth_method="oidc",
    )
    assert oidc_wildcard_principal.can("screening:read") is False


# -----------------------------------------------------------------------------
# Claim Normalization Tests
# -----------------------------------------------------------------------------


def test_claim_strings_handling():
    assert _claim_strings(["read", "write"]) == ["read", "write"]
    assert _claim_strings("read write  delete ") == ["read", "write", "delete"]
    assert _claim_strings("read, write,delete") == ["read", "write", "delete"]
    assert _claim_strings(None) == []
    assert _claim_strings(12345) == []


# -----------------------------------------------------------------------------
# OIDC Token Decoding Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decode_oidc_token_missing_settings(make_settings):
    settings = make_settings(auth_mode="oidc", oidc_jwks_url=None)
    with pytest.raises(HTTPException) as exc_info:
        await _decode_oidc_token("some.token.here", settings)
    assert exc_info.value.status_code == 503
    assert "OIDC configuration" in exc_info.value.detail


@pytest.mark.asyncio
async def test_decode_oidc_token_oversized(make_settings):
    settings = make_settings(
        auth_mode="oidc",
        oidc_issuer="https://issuer.com",
        oidc_audience="aud",
        oidc_jwks_url="https://issuer.com/.well-known/jwks.json",
    )
    oversized_token = "a" * 16385
    with pytest.raises(HTTPException) as exc_info:
        await _decode_oidc_token(oversized_token, settings)
    assert exc_info.value.status_code == 401
    assert "Oversized token" in exc_info.value.detail


@pytest.mark.asyncio
async def test_decode_oidc_token_invalid_jwt(make_settings):
    settings = make_settings(
        auth_mode="oidc",
        oidc_issuer="https://issuer.com",
        oidc_audience="aud",
        oidc_jwks_url="https://issuer.com/.well-known/jwks.json",
    )
    with patch("jwt.PyJWKClient") as mock_jwk_client:
        mock_jwk_client.return_value.get_signing_key_from_jwt.side_effect = (
            jwt.PyJWTError("Invalid token")
        )
        with pytest.raises(HTTPException) as exc_info:
            await _decode_oidc_token("invalid.jwt.token", settings)
        assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_decode_oidc_token_missing_subject_or_tenant(make_settings):
    settings = make_settings(
        auth_mode="oidc",
        oidc_issuer="https://issuer.com",
        oidc_audience="aud",
        oidc_jwks_url="https://issuer.com/.well-known/jwks.json",
    )
    mock_key = MagicMock()
    mock_key.key = "secret"

    # Missing subject (sub)
    payload_no_sub = {"tenant_id": "tenant-123"}
    with (
        patch("jwt.PyJWKClient.get_signing_key_from_jwt", return_value=mock_key),
        patch("jwt.decode", return_value=payload_no_sub),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await _decode_oidc_token("valid.token.payload", settings)
        assert exc_info.value.status_code == 403
        assert "subject" in exc_info.value.detail.lower()

    # Missing tenant_id
    payload_no_tenant = {"sub": "user-123"}
    with (
        patch("jwt.PyJWKClient.get_signing_key_from_jwt", return_value=mock_key),
        patch("jwt.decode", return_value=payload_no_tenant),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await _decode_oidc_token("valid.token.payload", settings)
        assert exc_info.value.status_code == 403
        assert "tenant" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_decode_oidc_token_success_and_scope_canonicalization(make_settings):
    settings = make_settings(
        auth_mode="oidc",
        oidc_issuer="https://issuer.com",
        oidc_audience="aud",
        oidc_jwks_url="https://issuer.com/.well-known/jwks.json",
    )
    mock_key = MagicMock()
    mock_key.key = "secret"

    payload = {
        "sub": "user-456",
        "tenant_id": "tenant-xyz",
        "roles": ["reviewer"],
        "scope": "screenings:read review:write",  # 'screenings:read' should map to 'screening:read'
    }

    with (
        patch("jwt.PyJWKClient.get_signing_key_from_jwt", return_value=mock_key),
        patch("jwt.decode", return_value=payload),
    ):
        principal = await _decode_oidc_token("valid.token.payload", settings)
        assert principal.subject == "user-456"
        assert principal.tenant_id == "tenant-xyz"
        assert principal.roles == ["reviewer"]
        assert "screening:read" in principal.permissions
        assert "review:write" in principal.permissions


# -----------------------------------------------------------------------------
# Authentication Resolver (get_principal) Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_principal_oidc_bearer_token(make_settings):
    settings = make_settings(
        auth_mode="oidc",
        oidc_issuer="https://issuer.com",
        oidc_audience="aud",
        oidc_jwks_url="https://issuer.com/.well-known/jwks.json",
    )
    mock_request = MagicMock()
    mock_request.headers = {"Authorization": "Bearer mock-oidc-token"}

    expected_principal = Principal(
        subject="user-1",
        tenant_id="tenant-1",
        roles=["user"],
        permissions=["screening:read"],
    )

    with patch(
        "app.core.security._decode_oidc_token",
        AsyncMock(return_value=expected_principal),
    ):
        principal = await get_principal(mock_request, settings)
        assert principal == expected_principal


@pytest.mark.asyncio
async def test_get_principal_api_key_header(make_settings):
    settings = make_settings(auth_mode="api_key", api_key="valid-secret-key")
    mock_request = MagicMock()
    mock_request.headers = {"X-API-Key": "valid-secret-key"}

    principal = await get_principal(mock_request, settings)
    assert principal.subject == "api-key-user"
    assert principal.tenant_id == "default"
    assert principal.auth_method == "api_key"
    assert principal.can("any:permission") is True


@pytest.mark.asyncio
async def test_get_principal_invalid_api_key(make_settings):
    settings = make_settings(auth_mode="api_key", api_key="valid-secret-key")
    mock_request = MagicMock()
    mock_request.headers = {"X-API-Key": "wrong-key"}

    with pytest.raises(HTTPException) as exc_info:
        await get_principal(mock_request, settings)
    assert exc_info.value.status_code == 401
    assert "Invalid API key" in exc_info.value.detail


@pytest.mark.asyncio
async def test_get_principal_development_fallback(make_settings):
    settings = make_settings(environment="development", auth_mode="development")
    mock_request = MagicMock()
    mock_request.headers = {}

    principal = await get_principal(mock_request, settings)
    assert principal.subject == "dev-user"
    assert principal.tenant_id == "local-dev"
    assert principal.auth_method == "development"


@pytest.mark.asyncio
async def test_get_principal_constant_time_api_key_check(make_settings):
    settings = make_settings(auth_mode="api_key", api_key="secret-key")
    mock_request = MagicMock()
    mock_request.headers = {"X-API-Key": "secret-key"}

    with patch("hmac.compare_digest", wraps=import_hmac_compare_digest) as mock_compare:
        await get_principal(mock_request, settings)
        assert mock_compare.called


def import_hmac_compare_digest(a, b):
    import hmac

    return hmac.compare_digest(a, b)


# -----------------------------------------------------------------------------
# Authorization Dependency Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_require_permission_dependency_success():
    principal = Principal(
        subject="user-1",
        tenant_id="tenant-a",
        roles=["reviewer"],
        permissions=["screening:read"],
    )
    dependency = require_permission("screening:read")
    res = await dependency(principal=principal)
    assert res == principal


@pytest.mark.asyncio
async def test_require_permission_dependency_forbidden():
    principal = Principal(
        subject="user-1",
        tenant_id="tenant-a",
        roles=["reviewer"],
        permissions=["screening:read"],
    )
    dependency = require_permission("audit:read")
    with pytest.raises(HTTPException) as exc_info:
        await dependency(principal=principal)
    assert exc_info.value.status_code == 403
    assert "Permission denied" in exc_info.value.detail
