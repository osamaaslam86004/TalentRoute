"""Unit tests for OIDC access token decoding, claim mapping, and security assertions."""

from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from jwt.exceptions import PyJWKClientConnectionError, PyJWTError
import pytest

from app.auth.oidc_auth import decode_access_token
from app.core.config import Settings
from app.core.security import Principal, _decode_oidc_token


class TestOIDCAuthDecoding:
    """Security tests for OIDC token verification and mapping logic."""

    def test_decode_token_unconfigured_oidc_raises_503() -> None:
        unconfigured_settings = Settings(
            auth_mode="auto",
            oidc_issuer=None,
            oidc_audience=None,
            oidc_jwks_url=None,
        )
        with pytest.raises(HTTPException) as exc_info:
            _decode_oidc_token("some-token", unconfigured_settings)
        assert exc_info.value.status_code == 503
        assert "OIDC authentication is not configured" in exc_info.value.detail

    def test_decode_oversized_token_raises_401(
        self, mock_settings: Settings
    ) -> None:
        oversized_token = "a" * 32769
        with pytest.raises(HTTPException) as exc_info:
            _decode_oidc_token(oversized_token, mock_settings)
        assert exc_info.value.status_code == 401
        assert "Invalid access token" in exc_info.value.detail

    @patch("app.core.security._jwks_client")
    @patch("jwt.decode")
    def test_successful_token_decoding_and_principal_mapping(
        self,
        mock_jwt_decode: MagicMock,
        mock_jwks_factory: MagicMock,
        mock_settings: Settings,
        mock_jwks_client: MagicMock,
        sample_jwt_claims: dict[str, object],
    ) -> None:
        mock_jwks_factory.return_value = mock_jwks_client
        mock_jwt_decode.return_value = sample_jwt_claims

        principal = _decode_oidc_token("valid.jwt.token", mock_settings)

        assert isinstance(principal, Principal)
        assert principal.subject == "user-456"
        assert principal.tenant_id == "tenant-alpha"
        assert "recruiter" in principal.roles
        assert "screening:write" in principal.scopes
        assert "screening:read" in principal.scopes
        assert principal.auth_method == "oidc"

    @patch("app.core.security._jwks_client")
    @patch("jwt.decode")
    def test_scope_alias_normalization(
        self,
        mock_jwt_decode: MagicMock,
        mock_jwks_factory: MagicMock,
        mock_settings: Settings,
        mock_jwks_client: MagicMock,
        sample_jwt_claims: dict[str, object],
    ) -> None:
        claims = dict(sample_jwt_claims)
        claims["scope"] = "screenings:read screenings:write screenings:delete"
        mock_jwks_factory.return_value = mock_jwks_client
        mock_jwt_decode.return_value = claims

        principal = _decode_oidc_token("valid.jwt.token", mock_settings)

        # Confirm plural aliases normalize to singular action scopes
        assert "screening:read" in principal.scopes
        assert "screening:write" in principal.scopes
        assert "screening:delete" in principal.scopes

    @patch("app.core.security._jwks_client")
    def test_jwks_connection_failure_raises_503(
        self,
        mock_jwks_factory: MagicMock,
        mock_settings: Settings,
    ) -> None:
        mock_jwks_client = MagicMock()
        mock_jwks_client.get_signing_key_from_jwt.side_effect = (
            PyJWKClientConnectionError("Connection timeout")
        )
        mock_jwks_factory.return_value = mock_jwks_client

        with pytest.raises(HTTPException) as exc_info:
            _decode_oidc_token("valid.jwt.token", mock_settings)

        assert exc_info.value.status_code == 503
        assert "Identity provider signing keys are unavailable" in exc_info.value.detail

    @patch("app.core.security._jwks_client")
    @patch("jwt.decode")
    def test_jwt_decode_error_raises_401(
        self,
        mock_jwt_decode: MagicMock,
        mock_jwks_factory: MagicMock,
        mock_settings: Settings,
        mock_jwks_client: MagicMock,
    ) -> None:
        mock_jwks_factory.return_value = mock_jwks_client
        mock_jwt_decode.side_effect = PyJWTError("Signature verification failed")

        with pytest.raises(HTTPException) as exc_info:
            _decode_oidc_token("invalid.jwt.signature", mock_settings)

        assert exc_info.value.status_code == 401
        assert "Invalid or expired access token" in exc_info.value.detail

    @patch("app.core.security._jwks_client")
    @patch("jwt.decode")
    def test_missing_subject_claim_raises_401(
        self,
        mock_jwt_decode: MagicMock,
        mock_jwks_factory: MagicMock,
        mock_settings: Settings,
        mock_jwks_client: MagicMock,
        sample_jwt_claims: dict[str, object],
    ) -> None:
        claims = dict(sample_jwt_claims)
        claims["sub"] = "  "  # Blank subject
        mock_jwks_factory.return_value = mock_jwks_client
        mock_jwt_decode.return_value = claims

        with pytest.raises(HTTPException) as exc_info:
            _decode_oidc_token("no.sub.jwt", mock_settings)

        assert exc_info.value.status_code == 401
        assert "Access token has no subject" in exc_info.value.detail

    @patch("app.core.security._jwks_client")
    @patch("jwt.decode")
    def test_missing_tenant_claim_when_required_raises_403(
        self,
        mock_jwt_decode: MagicMock,
        mock_jwks_factory: MagicMock,
        mock_settings: Settings,
        mock_jwks_client: MagicMock,
        sample_jwt_claims: dict[str, object],
    ) -> None:
        claims = dict(sample_jwt_claims)
        claims.pop("tenant_id", None)
        mock_jwks_factory.return_value = mock_jwks_client
        mock_jwt_decode.return_value = claims

        with pytest.raises(HTTPException) as exc_info:
            _decode_oidc_token("no.tenant.jwt", mock_settings)

        assert exc_info.value.status_code == 403
        assert "Access token is missing the tenant claim" in exc_info.value.detail

    @patch("app.core.security._jwks_client")
    @patch("jwt.decode")
    def test_invalid_tenant_claim_control_characters_raises_403(
        self,
        mock_jwt_decode: MagicMock,
        mock_jwks_factory: MagicMock,
        mock_settings: Settings,
        mock_jwks_client: MagicMock,
        sample_jwt_claims: dict[str, object],
    ) -> None:
        claims = dict(sample_jwt_claims)
        claims["tenant_id"] = "tenant\x00invalid"
        mock_jwks_factory.return_value = mock_jwks_client
        mock_jwt_decode.return_value = claims

        with pytest.raises(HTTPException) as exc_info:
            _decode_oidc_token("invalid.tenant.jwt", mock_settings)

        assert exc_info.value.status_code == 403
        assert "Access token has an invalid tenant claim" in exc_info.value.detail

    @patch("app.core.security._decode_oidc_token")
    def test_decode_access_token_wrapper(
        self, mock_decode_internal: MagicMock, mock_settings: Settings
    ) -> None:
        expected_principal = Principal(
            subject="wrapper-user",
            tenant_id="default",
            roles=frozenset({"recruiter"}),
            scopes=frozenset(),
            auth_method="oidc",
        )
        mock_decode_internal.return_value = expected_principal

        res = decode_access_token("some.jwt.token", settings=mock_settings)
        assert res == expected_principal
        mock_decode_internal.assert_called_once_with("some.jwt.token", mock_settings)
