"""Unit tests for ``app.core.security``: RBAC, claim parsing, OIDC decoding and FastAPI dependencies.

Everything is offline. ``jwt.decode`` and ``_jwks_client`` are mocked wherever claim handling (not
cryptography) is under test; real signature verification is covered in ``test_oidc_auth.py``.

NOTE: deliberately no ``from __future__ import annotations`` here, so FastAPI can resolve the
annotations of the small throw-away routes defined in the HTTP-level tests.
"""

import asyncio
import dataclasses
import hmac
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import jwt
import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError

from app.core import security
from app.core.security import (
    Principal,
    _claim_strings,
    _decode_oidc_token,
    get_principal,
    require_permission,
)
from app.observability.context import context_fields
from app.observability.policy import actor_hash

ISSUER = "https://issuer.example.com"
AUDIENCE = "screening-api"
JWKS_URL = "https://issuer.example.com/.well-known/jwks.json"

# The role -> permission matrix is the authorisation contract documented in the README. Pinning it
# here means any change to who can do what has to be made deliberately in this test too.
EXPECTED_PERMISSION_ROLES = {
    "screening:write": {"recruiter", "service"},
    "screening:read": {"recruiter", "reviewer", "auditor", "privacy_officer"},
    "screening:delete": {"privacy_officer"},
    "review:write": {"reviewer"},
    "review:read": {"reviewer", "auditor"},
    "review:appeal": {"compliance_officer"},
    "audit:read": {"auditor"},
    "evaluation:run": {"model_evaluator"},
}
ALL_ROLES = sorted(set().union(*EXPECTED_PERMISSION_ROLES.values()))

MISSING = object()  # sentinel: the claim is absent from the token (as opposed to None / blank)


# --------------------------------------------------------------------------- helpers / fixtures
def make_principal(
    *, roles=(), scopes=(), auth_method="oidc", subject="user-1", tenant_id="acme"
) -> Principal:
    return Principal(
        subject=subject,
        tenant_id=tenant_id,
        roles=frozenset(roles),
        scopes=frozenset(scopes),
        auth_method=auth_method,
    )


def make_request() -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace())


def bearer(token: str = "tok") -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


@pytest.fixture
def make_oidc_settings(make_settings):
    def build(**overrides):
        values = {
            "oidc_issuer": ISSUER,
            "oidc_audience": AUDIENCE,
            "oidc_jwks_url": JWKS_URL,
        }
        values.update(overrides)
        return make_settings(**values)

    return build


@pytest.fixture
def use_settings(monkeypatch, make_settings):
    """Make ``get_principal`` (which calls ``get_settings()`` itself) see our settings."""

    def apply(**overrides):
        settings = make_settings(**overrides)
        monkeypatch.setattr(security, "get_settings", lambda: settings)
        return settings

    return apply


@pytest.fixture
def auth_failures():
    with patch.object(security, "record_auth_failure") as recorder:
        yield recorder


@pytest.fixture
def jwt_env():
    """Mock the JWKS client and ``jwt.decode``; tests mutate ``claims`` to shape the token."""
    claims = {
        "sub": "user-1",
        "tenant_id": "acme",
        "roles": ["recruiter"],
        "scope": "screening:read",
    }
    signing_key = SimpleNamespace(key="PUBLIC-KEY")
    client = MagicMock()
    client.get_signing_key_from_jwt.return_value = signing_key

    with (
        patch.object(security, "_jwks_client", return_value=client) as factory,
        patch.object(security.jwt, "decode", return_value=claims) as decode,
    ):
        yield SimpleNamespace(
            claims=claims, client=client, factory=factory, decode=decode, signing_key=signing_key
        )


# =========================================================================== Principal.can
class TestPrincipalCan:
    def test_role_permission_matrix_is_pinned(self):
        assert {k: set(v) for k, v in security._PERMISSION_ROLES.items()} == (
            EXPECTED_PERMISSION_ROLES
        )

    @pytest.mark.parametrize(
        ("permission", "role"),
        [(p, r) for p, roles in EXPECTED_PERMISSION_ROLES.items() for r in sorted(roles)],
    )
    def test_each_documented_role_grants_its_permission(self, permission, role):
        assert make_principal(roles={role}).can(permission) is True

    @pytest.mark.parametrize(
        ("permission", "role"),
        [
            (p, r)
            for p, roles in EXPECTED_PERMISSION_ROLES.items()
            for r in ALL_ROLES
            if r not in roles
        ],
    )
    def test_other_roles_are_denied(self, permission, role):
        assert make_principal(roles={role}).can(permission) is False

    @pytest.mark.parametrize("auth_method", ["oidc", "api_key", "development", "other"])
    @pytest.mark.parametrize("permission", [*EXPECTED_PERMISSION_ROLES, "made:up"])
    def test_admin_role_grants_everything_for_every_auth_method(self, auth_method, permission):
        assert make_principal(roles={"admin"}, auth_method=auth_method).can(permission) is True

    def test_scope_grants_exactly_the_named_permission(self):
        principal = make_principal(scopes={"screening:read"})

        assert principal.can("screening:read") is True
        assert principal.can("screening:write") is False
        assert principal.can("audit:read") is False

    def test_scope_can_grant_a_permission_the_roles_do_not(self):
        principal = make_principal(roles={"recruiter"}, scopes={"audit:read"})

        assert principal.can("audit:read") is True

    @pytest.mark.parametrize("auth_method", ["api_key", "development"])
    def test_wildcard_scope_grants_everything_for_trusted_local_auth_methods(self, auth_method):
        principal = make_principal(scopes={"*"}, auth_method=auth_method)

        assert principal.can("screening:delete") is True
        assert principal.can("made:up") is True

    @pytest.mark.parametrize("auth_method", ["oidc", "something-else"])
    def test_wildcard_scope_is_ignored_for_other_auth_methods(self, auth_method):
        """A token claiming ``*`` must not become a super-user."""
        principal = make_principal(scopes={"*"}, auth_method=auth_method)

        assert principal.can("screening:read") is False
        assert principal.can("audit:read") is False

    def test_wildcard_with_a_non_wildcard_auth_method_still_honours_roles(self):
        principal = make_principal(roles={"auditor"}, scopes={"*"}, auth_method="oidc")

        assert principal.can("audit:read") is True
        assert principal.can("screening:delete") is False

    @pytest.mark.parametrize("permission", EXPECTED_PERMISSION_ROLES)
    def test_no_roles_and_no_scopes_is_denied_everything(self, permission):
        assert make_principal().can(permission) is False

    def test_unknown_permission_is_denied_for_non_admin_roles(self):
        assert make_principal(roles={"recruiter", "auditor"}).can("made:up") is False

    def test_any_one_qualifying_role_is_enough(self):
        principal = make_principal(roles={"nobody", "reviewer", "also-nobody"})

        assert principal.can("review:write") is True

    def test_roles_and_permissions_are_case_sensitive(self):
        assert make_principal(roles={"Admin"}).can("screening:read") is False
        assert make_principal(roles={"Recruiter"}).can("screening:write") is False
        assert make_principal(scopes={"Screening:Read"}).can("screening:read") is False

    def test_roles_do_not_leak_between_permissions(self):
        recruiter = make_principal(roles={"recruiter"})

        assert recruiter.can("screening:write") and recruiter.can("screening:read")
        assert not recruiter.can("screening:delete")
        assert not recruiter.can("review:write")

    def test_principal_is_immutable(self):
        principal = make_principal(roles={"recruiter"})

        with pytest.raises(dataclasses.FrozenInstanceError):
            principal.roles = frozenset({"admin"})  # type: ignore[misc]
        with pytest.raises(dataclasses.FrozenInstanceError):
            principal.tenant_id = "other"  # type: ignore[misc]

    def test_principal_uses_slots_and_supports_equality_and_hashing(self):
        a = make_principal(roles={"recruiter"})
        b = make_principal(roles={"recruiter"})

        assert not hasattr(a, "__dict__")
        assert a == b
        assert hash(a) == hash(b)
        assert a != make_principal(roles={"reviewer"})


# =========================================================================== _claim_strings
class TestClaimStrings:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("a b c", {"a", "b", "c"}),
            ("a,b,c", {"a", "b", "c"}),
            ("a, b  c", {"a", "b", "c"}),
            ("  spaced   out  ", {"spaced", "out"}),
            ("a\tb\nc", {"a", "b", "c"}),
            ("single", {"single"}),
            ("a a a", {"a"}),
            ("scope:read,scope:write scope:read", {"scope:read", "scope:write"}),
            ("", set()),
            ("   ", set()),
            (",,,", set()),
        ],
    )
    def test_strings_are_split_on_commas_and_whitespace(self, value, expected):
        assert _claim_strings(value) == frozenset(expected)

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (["a", "b"], {"a", "b"}),
            (("a", "b"), {"a", "b"}),
            ({"a", "b"}, {"a", "b"}),
            ([" a ", "b ", " "], {"a", "b"}),
            (["a", "", "  "], {"a"}),
            ([1, 2], {"1", "2"}),
            (["a", "a"], {"a"}),
            ([], set()),
            (["has space"], {"has space"}),  # list items are not re-split
        ],
    )
    def test_collections_are_stripped_and_blank_items_removed(self, value, expected):
        assert _claim_strings(value) == frozenset(expected)

    @pytest.mark.parametrize("value", [None, 5, 1.5, True, {"a": 1}, object(), b"bytes"])
    def test_unsupported_types_yield_an_empty_set(self, value):
        assert _claim_strings(value) == frozenset()

    def test_always_returns_a_frozenset(self):
        assert isinstance(_claim_strings("a b"), frozenset)
        assert isinstance(_claim_strings(["a"]), frozenset)
        assert isinstance(_claim_strings(None), frozenset)


# =========================================================================== _decode_oidc_token
class TestDecodeOidcTokenConfiguration:
    @pytest.mark.parametrize("missing", ["oidc_issuer", "oidc_audience", "oidc_jwks_url"])
    def test_any_missing_oidc_setting_yields_503(self, missing, make_oidc_settings, auth_failures):
        settings = make_oidc_settings().model_copy(update={missing: None})

        with patch.object(security, "_jwks_client") as factory:
            with pytest.raises(HTTPException) as raised:
                _decode_oidc_token("tok", settings)

        assert raised.value.status_code == 503
        assert raised.value.detail == "OIDC authentication is not configured"
        auth_failures.assert_called_once_with("jwks_unavailable")
        factory.assert_not_called()

    def test_completely_unconfigured_oidc_yields_503(self, make_settings):
        with pytest.raises(HTTPException) as raised:
            _decode_oidc_token("tok", make_settings())

        assert raised.value.status_code == 503


class TestDecodeOidcTokenSize:
    def test_oversized_token_is_rejected_before_any_key_lookup(
        self, make_oidc_settings, jwt_env, auth_failures
    ):
        with pytest.raises(HTTPException) as raised:
            _decode_oidc_token("x" * 32769, make_oidc_settings())

        assert raised.value.status_code == 401
        assert raised.value.detail == "Invalid access token"
        assert raised.value.headers == {"WWW-Authenticate": "Bearer"}
        auth_failures.assert_called_once_with("invalid")
        jwt_env.factory.assert_not_called()
        jwt_env.decode.assert_not_called()

    def test_token_of_exactly_the_maximum_size_is_still_processed(
        self, make_oidc_settings, jwt_env
    ):
        principal = _decode_oidc_token("x" * 32768, make_oidc_settings())

        assert principal.subject == "user-1"
        jwt_env.decode.assert_called_once()


class TestDecodeOidcTokenVerification:
    def test_valid_token_is_mapped_to_a_principal(self, make_oidc_settings, jwt_env):
        jwt_env.claims.update(
            {"roles": ["recruiter", "reviewer"], "scope": "screening:read screening:write"}
        )

        principal = _decode_oidc_token("tok", make_oidc_settings())

        assert principal == Principal(
            subject="user-1",
            tenant_id="acme",
            roles=frozenset({"recruiter", "reviewer"}),
            scopes=frozenset({"screening:read", "screening:write"}),
            auth_method="oidc",
        )

    def test_decoder_is_called_with_strict_verification_options(self, make_oidc_settings, jwt_env):
        settings = make_oidc_settings(oidc_algorithms=["RS256", "ES256"])

        _decode_oidc_token("the.token", settings)

        jwt_env.factory.assert_called_once_with(JWKS_URL)
        jwt_env.client.get_signing_key_from_jwt.assert_called_once_with("the.token")
        jwt_env.decode.assert_called_once_with(
            "the.token",
            "PUBLIC-KEY",
            algorithms=["RS256", "ES256"],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["exp", "iat", "sub"]},
            leeway=15,
        )

    def test_jwks_connection_failure_is_a_503_and_is_recorded(
        self, make_oidc_settings, jwt_env, auth_failures
    ):
        original = PyJWKClientConnectionError("dns failure for secret.internal")
        jwt_env.client.get_signing_key_from_jwt.side_effect = original

        with pytest.raises(HTTPException) as raised:
            _decode_oidc_token("tok", make_oidc_settings())

        assert raised.value.status_code == 503
        assert raised.value.detail == "Identity provider signing keys are unavailable"
        assert "secret.internal" not in raised.value.detail
        assert raised.value.__cause__ is original
        auth_failures.assert_called_once_with("jwks_unavailable")
        jwt_env.decode.assert_not_called()

    def test_unknown_key_id_is_a_401(self, make_oidc_settings, jwt_env, auth_failures):
        jwt_env.client.get_signing_key_from_jwt.side_effect = PyJWKClientError("kid not found")

        with pytest.raises(HTTPException) as raised:
            _decode_oidc_token("tok", make_oidc_settings())

        assert raised.value.status_code == 401
        assert raised.value.detail == "Invalid or expired access token"
        auth_failures.assert_called_once_with("invalid")

    @pytest.mark.parametrize(
        "error",
        [
            jwt.ExpiredSignatureError("Signature has expired"),
            jwt.InvalidAudienceError("Audience doesn't match"),
            jwt.InvalidIssuerError("Invalid issuer"),
            jwt.InvalidSignatureError("Signature verification failed"),
            jwt.DecodeError("Not enough segments"),
            jwt.MissingRequiredClaimError("sub"),
            jwt.InvalidAlgorithmError("The specified alg value is not allowed"),
            jwt.ImmatureSignatureError("not yet valid"),
            jwt.InvalidTokenError("generic"),
        ],
        ids=lambda e: type(e).__name__,
    )
    def test_jwt_validation_errors_are_a_generic_401(
        self, error, make_oidc_settings, jwt_env, auth_failures
    ):
        jwt_env.decode.side_effect = error

        with pytest.raises(HTTPException) as raised:
            _decode_oidc_token("tok", make_oidc_settings())

        assert raised.value.status_code == 401
        assert raised.value.detail == "Invalid or expired access token"
        assert raised.value.headers == {"WWW-Authenticate": "Bearer"}
        assert raised.value.__cause__ is error
        auth_failures.assert_called_once_with("invalid")

    def test_unexpected_errors_fail_closed_without_leaking_details(
        self, make_oidc_settings, jwt_env, auth_failures
    ):
        jwt_env.decode.side_effect = RuntimeError("super-secret internal detail")

        with pytest.raises(HTTPException) as raised:
            _decode_oidc_token("tok", make_oidc_settings())

        assert raised.value.status_code == 401
        assert raised.value.detail == "Unable to validate access token"
        assert "super-secret" not in raised.value.detail
        auth_failures.assert_called_once_with("invalid")

    def test_unexpected_error_during_key_lookup_also_fails_closed(
        self, make_oidc_settings, jwt_env
    ):
        jwt_env.client.get_signing_key_from_jwt.side_effect = OSError("socket exploded")

        with pytest.raises(HTTPException) as raised:
            _decode_oidc_token("tok", make_oidc_settings())

        assert raised.value.status_code == 401
        assert raised.value.detail == "Unable to validate access token"


class TestDecodeOidcTokenSubject:
    @pytest.mark.parametrize("subject", [MISSING, None, "", "   ", 123, ["u"]])
    def test_missing_or_blank_subject_is_a_401(
        self, subject, make_oidc_settings, jwt_env, auth_failures
    ):
        if subject is MISSING:
            jwt_env.claims.pop("sub")
        else:
            jwt_env.claims["sub"] = subject

        with pytest.raises(HTTPException) as raised:
            _decode_oidc_token("tok", make_oidc_settings())

        assert raised.value.status_code == 401
        assert raised.value.detail == "Access token has no subject"
        auth_failures.assert_called_once_with("invalid")

    def test_subject_is_validated_before_the_tenant(self, make_oidc_settings, jwt_env):
        jwt_env.claims.pop("sub")
        jwt_env.claims.pop("tenant_id")

        with pytest.raises(HTTPException) as raised:
            _decode_oidc_token("tok", make_oidc_settings())

        assert raised.value.status_code == 401


class TestDecodeOidcTokenTenant:
    def test_tenant_is_taken_from_the_claim_and_stripped(self, make_oidc_settings, jwt_env):
        jwt_env.claims["tenant_id"] = "  acme-corp  "

        assert _decode_oidc_token("tok", make_oidc_settings()).tenant_id == "acme-corp"

    @pytest.mark.parametrize("tenant", [MISSING, None, "", "   ", 123, ["acme"], {}])
    def test_missing_or_blank_tenant_is_forbidden_when_required(
        self, tenant, make_oidc_settings, jwt_env, auth_failures
    ):
        if tenant is MISSING:
            jwt_env.claims.pop("tenant_id")
        else:
            jwt_env.claims["tenant_id"] = tenant

        with pytest.raises(HTTPException) as raised:
            _decode_oidc_token("tok", make_oidc_settings())

        assert raised.value.status_code == 403
        assert raised.value.detail == "Access token is missing the tenant claim"
        auth_failures.assert_called_once_with("forbidden")

    @pytest.mark.parametrize("tenant", [MISSING, None, ""])
    def test_missing_tenant_falls_back_to_default_when_not_required(
        self, tenant, make_oidc_settings, jwt_env
    ):
        if tenant is MISSING:
            jwt_env.claims.pop("tenant_id")
        else:
            jwt_env.claims["tenant_id"] = tenant

        principal = _decode_oidc_token("tok", make_oidc_settings(oidc_require_tenant_claim=False))

        assert principal.tenant_id == "default"

    def test_non_string_tenant_is_stringified_when_not_required(self, make_oidc_settings, jwt_env):
        jwt_env.claims["tenant_id"] = 123

        principal = _decode_oidc_token("tok", make_oidc_settings(oidc_require_tenant_claim=False))

        assert principal.tenant_id == "123"

    @pytest.mark.parametrize("required", [True, False])
    @pytest.mark.parametrize(
        "tenant",
        ["t" * 129, "ac\nme", "ac\x00me", "ac\tme", "ac\rme", "\x1bacme"],
        ids=["too-long", "newline", "nul", "tab", "carriage-return", "escape"],
    )
    def test_malformed_tenant_is_forbidden(
        self, tenant, required, make_oidc_settings, jwt_env, auth_failures
    ):
        jwt_env.claims["tenant_id"] = tenant

        with pytest.raises(HTTPException) as raised:
            _decode_oidc_token("tok", make_oidc_settings(oidc_require_tenant_claim=required))

        assert raised.value.status_code == 403
        assert raised.value.detail == "Access token has an invalid tenant claim"
        auth_failures.assert_called_once_with("forbidden")

    def test_tenant_of_exactly_128_characters_is_accepted(self, make_oidc_settings, jwt_env):
        jwt_env.claims["tenant_id"] = "t" * 128

        assert _decode_oidc_token("tok", make_oidc_settings()).tenant_id == "t" * 128

    def test_tenant_with_inner_spaces_and_unicode_is_accepted(self, make_oidc_settings, jwt_env):
        jwt_env.claims["tenant_id"] = "Ünï Corp 42"

        assert _decode_oidc_token("tok", make_oidc_settings()).tenant_id == "Ünï Corp 42"

    def test_custom_tenant_claim_name_is_honoured(self, make_oidc_settings, jwt_env):
        jwt_env.claims.pop("tenant_id")
        jwt_env.claims["org"] = "globex"

        principal = _decode_oidc_token("tok", make_oidc_settings(oidc_tenant_claim="org"))

        assert principal.tenant_id == "globex"

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "BUG: with OIDC_REQUIRE_TENANT_CLAIM=false a whitespace-only tenant claim is truthy, so "
            "it skips the 'default' fallback and is stripped to tenant_id == ''. Expected 'default'."
        ),
    )
    def test_whitespace_only_tenant_falls_back_to_default_when_not_required(
        self, make_oidc_settings, jwt_env
    ):
        jwt_env.claims["tenant_id"] = "   "

        principal = _decode_oidc_token("tok", make_oidc_settings(oidc_require_tenant_claim=False))

        assert principal.tenant_id == "default"


class TestDecodeOidcTokenRolesAndScopes:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (["recruiter", "reviewer"], {"recruiter", "reviewer"}),
            ("recruiter reviewer", {"recruiter", "reviewer"}),
            ("recruiter,reviewer", {"recruiter", "reviewer"}),
            ("recruiter", {"recruiter"}),
            ([], set()),
            (None, set()),
            (42, set()),
        ],
    )
    def test_roles_are_normalised(self, raw, expected, make_oidc_settings, jwt_env):
        jwt_env.claims["roles"] = raw

        assert _decode_oidc_token("tok", make_oidc_settings()).roles == frozenset(expected)

    def test_missing_roles_claim_yields_no_roles(self, make_oidc_settings, jwt_env):
        jwt_env.claims.pop("roles")

        assert _decode_oidc_token("tok", make_oidc_settings()).roles == frozenset()

    def test_custom_roles_claim_name_is_honoured(self, make_oidc_settings, jwt_env):
        jwt_env.claims.pop("roles")
        jwt_env.claims["groups"] = "auditor"

        principal = _decode_oidc_token("tok", make_oidc_settings(oidc_roles_claim="groups"))

        assert principal.roles == frozenset({"auditor"})
        assert principal.can("audit:read")

    def test_roles_are_not_read_from_the_scope_claim(self, make_oidc_settings, jwt_env):
        jwt_env.claims.pop("roles")
        jwt_env.claims["scope"] = "admin"

        principal = _decode_oidc_token("tok", make_oidc_settings())

        assert "admin" not in principal.roles
        assert not principal.can("audit:read")

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("screening:read screening:write", {"screening:read", "screening:write"}),
            ("screening:read,screening:write", {"screening:read", "screening:write"}),
            (["screening:read"], {"screening:read"}),
            ("", set()),
        ],
    )
    def test_scopes_are_normalised(self, raw, expected, make_oidc_settings, jwt_env):
        jwt_env.claims["scope"] = raw

        assert _decode_oidc_token("tok", make_oidc_settings()).scopes == frozenset(expected)

    def test_no_scope_claims_at_all_yield_no_scopes(self, make_oidc_settings, jwt_env):
        jwt_env.claims.pop("scope")

        assert _decode_oidc_token("tok", make_oidc_settings()).scopes == frozenset()

    def test_scp_claim_is_used_when_the_default_scope_claim_is_absent(
        self, make_oidc_settings, jwt_env
    ):
        jwt_env.claims.pop("scope")
        jwt_env.claims["scp"] = "screening:read audit:read"

        principal = _decode_oidc_token("tok", make_oidc_settings())

        assert principal.scopes == frozenset({"screening:read", "audit:read"})

    def test_custom_scope_claim_wins_over_scp_and_scope(self, make_oidc_settings, jwt_env):
        jwt_env.claims.update(
            {"permissions": ["audit:read"], "scp": "ignored:a", "scope": "ignored:b"}
        )

        principal = _decode_oidc_token("tok", make_oidc_settings(oidc_scopes_claim="permissions"))

        assert principal.scopes == frozenset({"audit:read"})

    def test_absent_custom_scope_claim_falls_back_to_scp_before_scope(
        self, make_oidc_settings, jwt_env
    ):
        jwt_env.claims.update({"scp": "from:scp", "scope": "from:scope"})

        principal = _decode_oidc_token("tok", make_oidc_settings(oidc_scopes_claim="permissions"))

        assert principal.scopes == frozenset({"from:scp"})

    def test_absent_custom_scope_claim_falls_back_to_scope_when_no_scp(
        self, make_oidc_settings, jwt_env
    ):
        jwt_env.claims["scope"] = "from:scope"

        principal = _decode_oidc_token("tok", make_oidc_settings(oidc_scopes_claim="permissions"))

        assert principal.scopes == frozenset({"from:scope"})

    def test_plural_scope_spellings_are_aliased_and_originals_kept(
        self, make_oidc_settings, jwt_env
    ):
        jwt_env.claims["scope"] = "screenings:read screenings:write screenings:delete other:scope"

        principal = _decode_oidc_token("tok", make_oidc_settings())

        assert principal.scopes == frozenset(
            {
                "screenings:read",
                "screenings:write",
                "screenings:delete",
                "screening:read",
                "screening:write",
                "screening:delete",
                "other:scope",
            }
        )

    def test_aliasing_is_one_directional_and_limited_to_known_scopes(
        self, make_oidc_settings, jwt_env
    ):
        jwt_env.claims["scope"] = "screening:read screenings:admin"

        principal = _decode_oidc_token("tok", make_oidc_settings())

        assert principal.scopes == frozenset({"screening:read", "screenings:admin"})

    def test_aliased_scope_grants_the_canonical_permission_only(self, make_oidc_settings, jwt_env):
        jwt_env.claims["roles"] = []  # isolate scopes from the default 'recruiter' role
        jwt_env.claims["scope"] = "screenings:write"

        principal = _decode_oidc_token("tok", make_oidc_settings())

        assert principal.can("screening:write") is True
        assert principal.can("screening:read") is False
        assert principal.can("screening:delete") is False

    def test_wildcard_scope_in_a_token_does_not_grant_access(self, make_oidc_settings, jwt_env):
        jwt_env.claims["roles"] = []  # isolate scopes from the default 'recruiter' role
        jwt_env.claims["scope"] = "*"

        principal = _decode_oidc_token("tok", make_oidc_settings())

        assert principal.auth_method == "oidc"
        assert principal.can("screening:read") is False


# =========================================================================== _jwks_client
class TestJwksClientFactory:
    @pytest.fixture(autouse=True)
    def _fresh_cache(self):
        security._jwks_client.cache_clear()
        yield
        security._jwks_client.cache_clear()

    def test_client_is_cached_per_url_with_safe_defaults(self):
        with patch.object(security, "PyJWKClient") as constructor:
            constructor.side_effect = lambda *args, **kwargs: MagicMock()  # a new client per call
            first = security._jwks_client("https://a.example/jwks")
            again = security._jwks_client("https://a.example/jwks")
            other = security._jwks_client("https://b.example/jwks")

        assert first is again
        assert first is not other
        assert constructor.call_count == 2
        constructor.assert_any_call(
            "https://a.example/jwks", cache_jwk_set=True, lifespan=3600, timeout=5
        )


# =========================================================================== helpers: unauthorized / bind
class TestUnauthorizedAndBindHelpers:
    def test_unauthorized_defaults(self, auth_failures):
        error = security._unauthorized()

        assert error.status_code == 401
        assert error.detail == "Authentication required"
        assert error.headers == {"WWW-Authenticate": "Bearer"}
        auth_failures.assert_called_once_with("invalid")

    def test_unauthorized_records_the_given_reason(self, auth_failures):
        security._unauthorized("custom", reason="missing")

        auth_failures.assert_called_once_with("missing")

    def test_bind_principal_sets_request_state_and_telemetry_context(self, make_settings):
        request = make_request()
        principal = make_principal(subject="alice@example.com", tenant_id="acme")

        security._bind_principal(request, principal, make_settings())

        assert request.state.actor_subject == "alice@example.com"
        assert request.state.tenant_id == "acme"
        assert context_fields() == {"tenant_id": "acme"}  # no salt -> no actor identity at all

    def test_bind_principal_never_puts_the_raw_subject_into_telemetry(self, make_settings):
        settings = make_settings(telemetry_hash_salt="pepper")
        principal = make_principal(subject="alice@example.com")

        security._bind_principal(make_request(), principal, settings)

        fields = context_fields()
        assert fields["actor_hash"] == actor_hash("alice@example.com", settings)
        assert len(fields["actor_hash"]) == 16
        assert "alice" not in repr(fields)


# =========================================================================== get_principal
class TestGetPrincipalOidc:
    @pytest.mark.parametrize("mode", ["auto", "oidc"])
    async def test_bearer_token_is_decoded_off_the_event_loop(
        self, mode, use_settings, monkeypatch
    ):
        settings = use_settings(
            auth_mode=mode, oidc_issuer=ISSUER, oidc_audience=AUDIENCE, oidc_jwks_url=JWKS_URL
        )
        expected = make_principal(roles={"recruiter"}, tenant_id="acme")
        seen = {}

        def fake_decode(token, received_settings):
            seen["thread"] = threading.get_ident()
            seen["args"] = (token, received_settings)
            return expected

        monkeypatch.setattr(security, "_decode_oidc_token", fake_decode)
        request = make_request()

        principal = await get_principal(request, provided_key=None, bearer=bearer("abc.def"))

        assert principal is expected
        assert seen["args"] == ("abc.def", settings)
        assert seen["thread"] != threading.get_ident()  # blocking JWKS I/O must not stall the loop
        assert request.state.actor_subject == expected.subject
        assert request.state.tenant_id == "acme"
        assert context_fields()["tenant_id"] == "acme"

    async def test_bearer_takes_precedence_over_a_valid_api_key(self, use_settings, monkeypatch):
        use_settings(auth_mode="auto", api_key="the-secret")
        decoded = make_principal()
        decode = MagicMock(return_value=decoded)
        monkeypatch.setattr(security, "_decode_oidc_token", decode)

        principal = await get_principal(make_request(), provided_key="the-secret", bearer=bearer())

        assert principal is decoded
        assert principal.auth_method == "oidc"
        decode.assert_called_once()

    @pytest.mark.parametrize("status_code", [401, 403, 503])
    async def test_failed_bearer_never_falls_back_to_the_api_key(
        self, status_code, use_settings, monkeypatch
    ):
        use_settings(auth_mode="auto", api_key="the-secret")
        error = HTTPException(status_code=status_code, detail="rejected")
        monkeypatch.setattr(security, "_decode_oidc_token", MagicMock(side_effect=error))

        with pytest.raises(HTTPException) as raised:
            await get_principal(make_request(), provided_key="the-secret", bearer=bearer())

        assert raised.value is error

    async def test_oidc_mode_without_a_bearer_is_unauthorised(self, use_settings, auth_failures):
        use_settings(
            auth_mode="oidc", oidc_issuer=ISSUER, oidc_audience=AUDIENCE, oidc_jwks_url=JWKS_URL
        )

        with pytest.raises(HTTPException) as raised:
            await get_principal(make_request(), provided_key=None, bearer=None)

        assert raised.value.status_code == 401
        assert raised.value.detail == "Authentication required"
        auth_failures.assert_called_once_with("missing")

    async def test_oidc_mode_records_invalid_when_only_an_api_key_is_offered(
        self, use_settings, auth_failures
    ):
        use_settings(
            auth_mode="oidc",
            api_key="the-secret",
            oidc_issuer=ISSUER,
            oidc_audience=AUDIENCE,
            oidc_jwks_url=JWKS_URL,
        )

        with pytest.raises(HTTPException) as raised:
            await get_principal(make_request(), provided_key="the-secret", bearer=None)

        assert raised.value.status_code == 401  # API keys are never accepted in oidc mode
        auth_failures.assert_called_once_with("invalid")


class TestGetPrincipalApiKey:
    async def test_valid_key_yields_the_api_key_principal(self, use_settings):
        use_settings(auth_mode="api_key", api_key="the-secret")
        request = make_request()

        principal = await get_principal(request, provided_key="the-secret", bearer=None)

        assert principal == Principal(
            subject="api-key-client",
            tenant_id="default",
            roles=frozenset({"admin"}),
            scopes=frozenset({"*"}),
            auth_method="api_key",
        )
        assert principal.can("screening:delete")
        assert request.state.actor_subject == "api-key-client"
        assert request.state.tenant_id == "default"
        assert context_fields() == {"tenant_id": "default"}

    async def test_key_comparison_uses_hmac_compare_digest(self, use_settings):
        use_settings(auth_mode="api_key", api_key="the-secret")

        with patch.object(security.hmac, "compare_digest", wraps=hmac.compare_digest) as compare:
            await get_principal(make_request(), provided_key="the-secret", bearer=None)

        compare.assert_called_once_with("the-secret", "the-secret")

    async def test_actor_hash_is_bound_when_a_salt_is_configured(self, use_settings):
        settings = use_settings(auth_mode="api_key", api_key="k", telemetry_hash_salt="pepper")

        await get_principal(make_request(), provided_key="k", bearer=None)

        assert context_fields()["actor_hash"] == actor_hash("api-key-client", settings)

    @pytest.mark.parametrize(
        "wrong", ["wrong", "the-secre", "the-secret ", "THE-SECRET", "the-secret-extra", " "]
    )
    async def test_wrong_keys_are_rejected(self, wrong, use_settings, auth_failures):
        use_settings(auth_mode="api_key", api_key="the-secret")

        with pytest.raises(HTTPException) as raised:
            await get_principal(make_request(), provided_key=wrong, bearer=None)

        assert raised.value.status_code == 401
        assert raised.value.detail == "Invalid or missing credentials"
        assert raised.value.headers == {"WWW-Authenticate": "Bearer"}
        auth_failures.assert_called_once_with("invalid")

    @pytest.mark.parametrize("provided", [None, ""])
    async def test_absent_key_is_reported_as_missing(self, provided, use_settings, auth_failures):
        use_settings(auth_mode="api_key", api_key="the-secret")

        with pytest.raises(HTTPException) as raised:
            await get_principal(make_request(), provided_key=provided, bearer=None)

        assert raised.value.status_code == 401
        auth_failures.assert_called_once_with("missing")

    async def test_bearer_is_ignored_in_api_key_mode(
        self, use_settings, monkeypatch, auth_failures
    ):
        use_settings(auth_mode="api_key", api_key="the-secret")
        decode = MagicMock()
        monkeypatch.setattr(security, "_decode_oidc_token", decode)

        with pytest.raises(HTTPException) as raised:
            await get_principal(make_request(), provided_key=None, bearer=bearer())

        assert raised.value.status_code == 401
        decode.assert_not_called()
        auth_failures.assert_called_once_with("invalid")  # a credential was offered, just unusable

    async def test_valid_key_still_works_when_a_bearer_is_also_sent_in_api_key_mode(
        self, use_settings, monkeypatch
    ):
        use_settings(auth_mode="api_key", api_key="the-secret")
        decode = MagicMock()
        monkeypatch.setattr(security, "_decode_oidc_token", decode)

        principal = await get_principal(make_request(), provided_key="the-secret", bearer=bearer())

        assert principal.auth_method == "api_key"
        decode.assert_not_called()

    async def test_configured_key_without_credentials_is_unauthorised_even_in_development(
        self, use_settings, auth_failures
    ):
        use_settings(auth_mode="auto", api_key="the-secret", environment="development")

        with pytest.raises(HTTPException) as raised:
            await get_principal(make_request(), provided_key=None, bearer=None)

        assert raised.value.status_code == 401
        auth_failures.assert_called_once_with("missing")

    async def test_wrong_key_with_a_configured_key_never_reaches_the_dev_fallback(
        self, use_settings
    ):
        use_settings(auth_mode="auto", api_key="the-secret", environment="test")

        with pytest.raises(HTTPException):
            await get_principal(make_request(), provided_key="nope", bearer=None)

    @pytest.mark.xfail(
        strict=True,
        raises=TypeError,
        reason=(
            "BUG: hmac.compare_digest() raises TypeError for str arguments containing non-ASCII "
            "characters (HTTP headers are latin-1 decoded), so an unauthenticated client can turn "
            "a 401 into a 500. Compare bytes (e.g. .encode('utf-8')) instead."
        ),
    )
    async def test_non_ascii_api_key_header_is_rejected_with_401_not_a_crash(self, use_settings):
        use_settings(auth_mode="api_key", api_key="the-secret")

        with pytest.raises(HTTPException) as raised:
            await get_principal(make_request(), provided_key="caf\u00e9", bearer=None)

        assert raised.value.status_code == 401


class TestGetPrincipalDevelopmentFallback:
    @pytest.mark.parametrize("environment", ["development", "test"])
    async def test_auto_mode_without_any_key_grants_local_admin(self, environment, use_settings):
        use_settings(auth_mode="auto", environment=environment)
        request = make_request()

        principal = await get_principal(request, provided_key=None, bearer=None)

        assert principal == Principal(
            subject="local-development",
            tenant_id="default",
            roles=frozenset({"admin"}),
            scopes=frozenset({"*"}),
            auth_method="development",
        )
        assert principal.can("anything:at:all")
        assert request.state.actor_subject == "local-development"
        assert context_fields() == {"tenant_id": "default"}

    async def test_staging_has_no_development_fallback(self, use_settings, auth_failures):
        use_settings(auth_mode="auto", environment="staging")

        with pytest.raises(HTTPException) as raised:
            await get_principal(make_request(), provided_key=None, bearer=None)

        assert raised.value.status_code == 401
        assert raised.value.detail == "Invalid or missing credentials"
        auth_failures.assert_called_once_with("missing")

    async def test_oidc_configured_without_a_token_does_not_fall_back_in_staging(
        self, use_settings
    ):
        use_settings(
            auth_mode="auto",
            environment="staging",
            oidc_issuer=ISSUER,
            oidc_audience=AUDIENCE,
            oidc_jwks_url=JWKS_URL,
        )

        with pytest.raises(HTTPException) as raised:
            await get_principal(make_request(), provided_key=None, bearer=None)

        assert raised.value.status_code == 401


# =========================================================================== require_permission
class TestRequirePermission:
    async def test_principal_with_the_permission_is_returned(self):
        auditor = make_principal(roles={"auditor"})

        result = await require_permission("audit:read")(principal=auditor)

        assert result is auditor

    async def test_principal_without_the_permission_gets_403(self, auth_failures):
        recruiter = make_principal(roles={"recruiter"})

        with pytest.raises(HTTPException) as raised:
            await require_permission("audit:read")(principal=recruiter)

        assert raised.value.status_code == 403
        assert raised.value.detail == "Insufficient permissions"
        auth_failures.assert_called_once_with("forbidden")

    async def test_successful_check_records_no_auth_failure(self, auth_failures):
        await require_permission("audit:read")(principal=make_principal(roles={"auditor"}))

        auth_failures.assert_not_called()

    async def test_admin_passes_any_permission(self):
        admin = make_principal(roles={"admin"})

        for permission in (*EXPECTED_PERMISSION_ROLES, "made:up"):
            assert await require_permission(permission)(principal=admin) is admin

    async def test_scope_based_access_is_honoured(self):
        principal = make_principal(scopes={"review:read"})

        assert await require_permission("review:read")(principal=principal) is principal

    async def test_dependencies_are_bound_to_their_own_permission(self):
        reviewer = make_principal(roles={"reviewer"})
        read_dep = require_permission("review:read")
        appeal_dep = require_permission("review:appeal")

        assert await read_dep(principal=reviewer) is reviewer
        with pytest.raises(HTTPException) as raised:
            await appeal_dep(principal=reviewer)
        assert raised.value.status_code == 403

    @pytest.mark.parametrize(
        ("permission", "name"),
        [
            ("audit:read", "require_audit_read"),
            ("screening:write", "require_screening_write"),
            ("review:appeal", "require_review_appeal"),
        ],
    )
    def test_dependency_function_is_named_after_the_permission(self, permission, name):
        assert require_permission(permission).__name__ == name

    def test_each_call_returns_a_distinct_dependency(self):
        assert require_permission("audit:read") is not require_permission("audit:read")

    def test_dependency_declares_get_principal_as_its_sub_dependency(self):
        default = require_permission("audit:read").__defaults__[0]

        assert default.dependency is get_principal

    async def test_dependency_is_a_coroutine_function(self):
        assert asyncio.iscoroutinefunction(require_permission("audit:read"))


# =========================================================================== HTTP-level wiring
def build_app() -> FastAPI:
    app = FastAPI()

    @app.get("/me")
    async def me(principal: Principal = Depends(get_principal)):
        return {"subject": principal.subject, "method": principal.auth_method}

    @app.get("/audit")
    async def audit(principal: Principal = Depends(require_permission("audit:read"))):
        return {"subject": principal.subject}

    return app


async def get(path: str, headers: dict[str, str] | None = None) -> httpx.Response:
    transport = httpx.ASGITransport(app=build_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path, headers=headers)


class TestHttpWiring:
    async def test_x_api_key_header_authenticates(self, use_settings):
        use_settings(auth_mode="api_key", api_key="the-secret")

        response = await get("/me", {"X-API-Key": "the-secret"})

        assert response.status_code == 200
        assert response.json() == {"subject": "api-key-client", "method": "api_key"}

    async def test_missing_credentials_yield_401_with_www_authenticate(self, use_settings):
        use_settings(auth_mode="api_key", api_key="the-secret")

        response = await get("/me")

        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.json() == {"detail": "Invalid or missing credentials"}

    async def test_wrong_api_key_yields_401(self, use_settings):
        use_settings(auth_mode="api_key", api_key="the-secret")

        assert (await get("/me", {"X-API-Key": "nope"})).status_code == 401

    async def test_authorization_bearer_header_reaches_the_decoder(self, use_settings, monkeypatch):
        use_settings(auth_mode="auto", api_key="the-secret")
        decode = MagicMock(return_value=make_principal(subject="oidc-user", roles={"auditor"}))
        monkeypatch.setattr(security, "_decode_oidc_token", decode)

        response = await get("/me", {"Authorization": "Bearer abc.def.ghi"})

        assert response.status_code == 200
        assert response.json() == {"subject": "oidc-user", "method": "oidc"}
        assert decode.call_args.args[0] == "abc.def.ghi"

    async def test_non_bearer_authorization_scheme_is_ignored(self, use_settings, monkeypatch):
        use_settings(auth_mode="auto", api_key="the-secret")
        decode = MagicMock()
        monkeypatch.setattr(security, "_decode_oidc_token", decode)

        response = await get("/me", {"Authorization": "Basic dXNlcjpwYXNz"})

        assert response.status_code == 401
        decode.assert_not_called()

    async def test_development_fallback_works_without_headers(self, use_settings):
        use_settings(auth_mode="auto", environment="development")

        response = await get("/me")

        assert response.status_code == 200
        assert response.json()["method"] == "development"

    async def test_permitted_role_reaches_a_protected_route(self, use_settings, monkeypatch):
        use_settings(auth_mode="auto", api_key="the-secret")
        monkeypatch.setattr(
            security,
            "_decode_oidc_token",
            MagicMock(return_value=make_principal(subject="auditor-1", roles={"auditor"})),
        )

        response = await get("/audit", {"Authorization": "Bearer t"})

        assert response.status_code == 200
        assert response.json() == {"subject": "auditor-1"}

    async def test_unpermitted_role_gets_403_on_a_protected_route(self, use_settings, monkeypatch):
        use_settings(auth_mode="auto", api_key="the-secret")
        monkeypatch.setattr(
            security,
            "_decode_oidc_token",
            MagicMock(return_value=make_principal(roles={"recruiter"})),
        )

        response = await get("/audit", {"Authorization": "Bearer t"})

        assert response.status_code == 403
        assert response.json() == {"detail": "Insufficient permissions"}

    async def test_unauthenticated_request_gets_401_not_403_on_a_protected_route(
        self, use_settings
    ):
        use_settings(auth_mode="api_key", api_key="the-secret")

        assert (await get("/audit")).status_code == 401

    async def test_api_key_admin_can_reach_a_protected_route(self, use_settings):
        use_settings(auth_mode="api_key", api_key="the-secret")

        response = await get("/audit", {"X-API-Key": "the-secret"})

        assert response.status_code == 200
