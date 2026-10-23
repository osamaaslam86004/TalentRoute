"""Unit tests for ``app.auth.oidc_auth``.

Two layers:

* ``TestForwarding`` mocks ``_decode_oidc_token`` and proves ``decode_access_token`` is a thin,
  correct wrapper (settings resolution, return value, error propagation, public exports).
* ``TestEndToEndVerification`` pushes *real* RSA-signed JWTs through the wrapper and the real
  ``_decode_oidc_token`` so PyJWT's signature / issuer / audience / expiry / algorithm checks are
  genuinely exercised. Only the JWKS lookup is mocked, so no network request is ever made.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from app.auth import oidc_auth
from app.auth.oidc_auth import Principal, decode_access_token
from app.core import security

ISSUER = "https://issuer.example.com"
AUDIENCE = "screening-api"
JWKS_URL = "https://issuer.example.com/.well-known/jwks.json"

_REMOVE = object()  # sentinel: drop a claim from the generated token


def make_principal(**overrides) -> Principal:
    values = {
        "subject": "user-1",
        "tenant_id": "acme",
        "roles": frozenset({"recruiter"}),
        "scopes": frozenset({"screening:read"}),
        "auth_method": "oidc",
    }
    values.update(overrides)
    return Principal(**values)


# =========================================================================== forwarding
class TestForwarding:
    def test_forwards_token_and_explicit_settings_and_returns_the_principal(self, make_settings):
        settings = make_settings()
        expected = make_principal()

        with patch.object(oidc_auth, "_decode_oidc_token", return_value=expected) as decode:
            result = decode_access_token("the.jwt.token", settings)

        decode.assert_called_once_with("the.jwt.token", settings)
        assert result is expected

    def test_falls_back_to_get_settings_when_none_is_supplied(self):
        sentinel = object()
        expected = make_principal()

        with (
            patch.object(oidc_auth, "get_settings", return_value=sentinel) as get_settings,
            patch.object(oidc_auth, "_decode_oidc_token", return_value=expected) as decode,
        ):
            result = decode_access_token("tok")

        get_settings.assert_called_once_with()
        decode.assert_called_once_with("tok", sentinel)
        assert result is expected

    def test_explicit_settings_bypass_get_settings(self, make_settings):
        settings = make_settings()

        with (
            patch.object(oidc_auth, "get_settings") as get_settings,
            patch.object(oidc_auth, "_decode_oidc_token", return_value=make_principal()),
        ):
            decode_access_token("tok", settings)

        get_settings.assert_not_called()

    def test_explicit_none_settings_also_use_get_settings(self):
        with (
            patch.object(oidc_auth, "get_settings", return_value="cfg") as get_settings,
            patch.object(oidc_auth, "_decode_oidc_token", return_value=make_principal()),
        ):
            decode_access_token("tok", None)

        get_settings.assert_called_once_with()

    @pytest.mark.parametrize("status_code", [401, 403, 503])
    def test_http_exceptions_from_the_decoder_propagate_unchanged(self, status_code, make_settings):
        error = HTTPException(status_code=status_code, detail="boom")

        with patch.object(oidc_auth, "_decode_oidc_token", side_effect=error):
            with pytest.raises(HTTPException) as raised:
                decode_access_token("tok", make_settings())

        assert raised.value is error

    def test_unexpected_exceptions_are_not_swallowed(self, make_settings):
        with patch.object(oidc_auth, "_decode_oidc_token", side_effect=RuntimeError("bug")):
            with pytest.raises(RuntimeError, match="bug"):
                decode_access_token("tok", make_settings())

    def test_public_exports(self):
        assert oidc_auth.__all__ == ["Principal", "decode_access_token"]
        assert Principal is security.Principal

    def test_wrapper_uses_the_real_decoder_by_default(self):
        assert oidc_auth._decode_oidc_token is security._decode_oidc_token

    def test_unconfigured_oidc_surfaces_a_503_through_the_wrapper(self, make_settings):
        with pytest.raises(HTTPException) as raised:
            decode_access_token("tok", make_settings())  # no issuer/audience/jwks configured

        assert raised.value.status_code == 503
        assert raised.value.detail == "OIDC authentication is not configured"


# =========================================================================== real JWT round-trips
@pytest.fixture(scope="module")
def private_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def other_private_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def oidc_settings(make_settings):
    return make_settings(oidc_issuer=ISSUER, oidc_audience=AUDIENCE, oidc_jwks_url=JWKS_URL)


@pytest.fixture
def jwks(private_key):
    """Replace the cached JWKS client so the token's public key is resolved with no network."""
    client = MagicMock()
    client.get_signing_key_from_jwt.return_value = SimpleNamespace(key=private_key.public_key())
    with patch.object(security, "_jwks_client", return_value=client) as factory:
        yield SimpleNamespace(client=client, factory=factory)


def make_token(key, *, algorithm: str = "RS256", **overrides) -> str:
    now = datetime.now(UTC)
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-1",
        "tenant_id": "acme",
        "roles": ["recruiter"],
        "scope": "screenings:read screening:write",
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    for name, value in overrides.items():
        if value is _REMOVE:
            claims.pop(name, None)
        else:
            claims[name] = value
    return jwt.encode(claims, key, algorithm=algorithm)


def assert_rejected(raised, *, status_code: int, detail: str) -> None:
    assert raised.value.status_code == status_code
    assert raised.value.detail == detail


class TestEndToEndVerification:
    def test_valid_token_yields_a_populated_principal(self, private_key, oidc_settings, jwks):
        token = make_token(private_key)

        principal = decode_access_token(token, oidc_settings)

        assert principal == Principal(
            subject="user-1",
            tenant_id="acme",
            roles=frozenset({"recruiter"}),
            scopes=frozenset({"screenings:read", "screening:read", "screening:write"}),
            auth_method="oidc",
        )
        assert principal.can("screening:write")

    def test_signing_key_is_looked_up_for_the_configured_jwks_url_and_this_token(
        self, private_key, oidc_settings, jwks
    ):
        token = make_token(private_key)

        decode_access_token(token, oidc_settings)

        jwks.factory.assert_called_once_with(JWKS_URL)
        jwks.client.get_signing_key_from_jwt.assert_called_once_with(token)

    def test_token_expired_beyond_the_leeway_is_rejected(self, private_key, oidc_settings, jwks):
        token = make_token(private_key, exp=datetime.now(UTC) - timedelta(minutes=1))

        with pytest.raises(HTTPException) as raised:
            decode_access_token(token, oidc_settings)

        assert_rejected(raised, status_code=401, detail="Invalid or expired access token")
        assert raised.value.headers == {"WWW-Authenticate": "Bearer"}

    def test_token_expired_within_the_15_second_leeway_is_accepted(
        self, private_key, oidc_settings, jwks
    ):
        token = make_token(private_key, exp=datetime.now(UTC) - timedelta(seconds=5))

        assert decode_access_token(token, oidc_settings).subject == "user-1"

    def test_wrong_audience_is_rejected(self, private_key, oidc_settings, jwks):
        token = make_token(private_key, aud="some-other-api")

        with pytest.raises(HTTPException) as raised:
            decode_access_token(token, oidc_settings)

        assert_rejected(raised, status_code=401, detail="Invalid or expired access token")

    def test_missing_audience_is_rejected(self, private_key, oidc_settings, jwks):
        token = make_token(private_key, aud=_REMOVE)

        with pytest.raises(HTTPException) as raised:
            decode_access_token(token, oidc_settings)

        assert raised.value.status_code == 401

    def test_wrong_issuer_is_rejected(self, private_key, oidc_settings, jwks):
        token = make_token(private_key, iss="https://evil.example.com")

        with pytest.raises(HTTPException) as raised:
            decode_access_token(token, oidc_settings)

        assert_rejected(raised, status_code=401, detail="Invalid or expired access token")

    def test_token_signed_by_a_different_key_is_rejected(
        self, other_private_key, oidc_settings, jwks
    ):
        token = make_token(other_private_key)  # JWKS only knows the *first* key's public half

        with pytest.raises(HTTPException) as raised:
            decode_access_token(token, oidc_settings)

        assert_rejected(raised, status_code=401, detail="Invalid or expired access token")

    def test_payload_tampering_invalidates_the_signature(self, private_key, oidc_settings, jwks):
        genuine = make_token(private_key, sub="user-1")
        forged_payload = make_token(private_key, sub="admin-user").split(".")[1]
        header, _, signature = genuine.split(".")

        with pytest.raises(HTTPException) as raised:
            decode_access_token(f"{header}.{forged_payload}.{signature}", oidc_settings)

        assert raised.value.status_code == 401

    @pytest.mark.parametrize("claim", ["exp", "iat", "sub"])
    def test_required_claims_must_be_present(self, claim, private_key, oidc_settings, jwks):
        token = make_token(private_key, **{claim: _REMOVE})

        with pytest.raises(HTTPException) as raised:
            decode_access_token(token, oidc_settings)

        assert_rejected(raised, status_code=401, detail="Invalid or expired access token")

    def test_symmetric_algorithm_confusion_is_rejected(self, oidc_settings, jwks):
        """An HS256 token must never be accepted when only RS256 is allow-listed."""
        token = make_token("s" * 64, algorithm="HS256")

        with pytest.raises(HTTPException) as raised:
            decode_access_token(token, oidc_settings)

        assert raised.value.status_code == 401

    def test_unsigned_alg_none_token_is_rejected(self, oidc_settings, jwks):
        token = make_token(None, algorithm="none")

        with pytest.raises(HTTPException) as raised:
            decode_access_token(token, oidc_settings)

        assert raised.value.status_code == 401

    def test_only_configured_algorithms_are_accepted(self, private_key, make_settings, jwks):
        settings = make_settings(
            oidc_issuer=ISSUER,
            oidc_audience=AUDIENCE,
            oidc_jwks_url=JWKS_URL,
            oidc_algorithms=["RS512"],
        )

        with pytest.raises(HTTPException) as raised:
            decode_access_token(make_token(private_key, algorithm="RS256"), settings)
        assert raised.value.status_code == 401

        assert decode_access_token(
            make_token(private_key, algorithm="RS512"), settings
        ).subject == ("user-1")

    def test_garbage_token_is_rejected_with_a_generic_message(self, oidc_settings, jwks):
        with pytest.raises(HTTPException) as raised:
            decode_access_token("definitely-not-a-jwt", oidc_settings)

        assert_rejected(raised, status_code=401, detail="Invalid or expired access token")

    def test_error_detail_never_echoes_the_token_or_library_message(
        self, private_key, oidc_settings, jwks
    ):
        token = make_token(private_key, aud="wrong")

        with pytest.raises(HTTPException) as raised:
            decode_access_token(token, oidc_settings)

        assert token not in str(raised.value.detail)
        assert "audience" not in str(raised.value.detail).lower()

    def test_missing_tenant_claim_is_forbidden_by_default(self, private_key, oidc_settings, jwks):
        token = make_token(private_key, tenant_id=_REMOVE)

        with pytest.raises(HTTPException) as raised:
            decode_access_token(token, oidc_settings)

        assert_rejected(raised, status_code=403, detail="Access token is missing the tenant claim")

    def test_missing_tenant_maps_to_default_when_not_required(
        self, private_key, make_settings, jwks
    ):
        settings = make_settings(
            oidc_issuer=ISSUER,
            oidc_audience=AUDIENCE,
            oidc_jwks_url=JWKS_URL,
            oidc_require_tenant_claim=False,
        )

        principal = decode_access_token(make_token(private_key, tenant_id=_REMOVE), settings)

        assert principal.tenant_id == "default"

    def test_jwks_outage_surfaces_as_503(self, private_key, oidc_settings, jwks):
        jwks.client.get_signing_key_from_jwt.side_effect = jwt.PyJWKClientConnectionError("down")

        with pytest.raises(HTTPException) as raised:
            decode_access_token(make_token(private_key), oidc_settings)

        assert_rejected(
            raised, status_code=503, detail="Identity provider signing keys are unavailable"
        )

    def test_unknown_signing_key_id_surfaces_as_401(self, private_key, oidc_settings, jwks):
        jwks.client.get_signing_key_from_jwt.side_effect = jwt.PyJWKClientError("no matching kid")

        with pytest.raises(HTTPException) as raised:
            decode_access_token(make_token(private_key), oidc_settings)

        assert_rejected(raised, status_code=401, detail="Invalid or expired access token")
