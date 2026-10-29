"""Unit tests for Principal permission evaluation and role-based access control."""

import pytest

from app.core.security import Principal, _claim_strings


class TestClaimStrings:
    """Tests for claim parsing and normalization utility."""

    def test_claim_strings_from_single_string(self) -> None:
        result = _claim_strings("role1 role2, role3")
        assert result == frozenset({"role1", "role2", "role3"})

    def test_claim_strings_from_list(self) -> None:
        result = _claim_strings([" recruiter ", "reviewer", ""])
        assert result == frozenset({"recruiter", "reviewer"})

    def test_claim_strings_from_invalid_type(self) -> None:
        assert _claim_strings(12345) == frozenset()
        assert _claim_strings(None) == frozenset()


class TestPrincipalRBAC:
    """Tests for Principal.can() permission decision logic."""

    def test_admin_role_escalation_grants_all_permissions() -> None:
        principal = Principal(
            subject="admin-user",
            tenant_id="tenant-alpha",
            roles=frozenset({"admin"}),
            scopes=frozenset(),
            auth_method="oidc",
        )
        assert principal.can("screening:write") is True
        assert principal.can("screening:delete") is True
        assert principal.can("audit:read") is True
        assert principal.can("arbitrary:custom_permission") is True

    def test_direct_scope_grant() -> None:
        principal = Principal(
            subject="scoped-user",
            tenant_id="tenant-alpha",
            roles=frozenset(),
            scopes=frozenset({"screening:write"}),
            auth_method="oidc",
        )
        assert principal.can("screening:write") is True
        assert principal.can("screening:delete") is False

    def test_wildcard_scope_for_api_key_and_dev_auth() -> None:
        api_key_principal = Principal(
            subject="api-key-client",
            tenant_id="default",
            roles=frozenset(),
            scopes=frozenset({"*"}),
            auth_method="api_key",
        )
        dev_principal = Principal(
            subject="local-dev",
            tenant_id="default",
            roles=frozenset(),
            scopes=frozenset({"*"}),
            auth_method="development",
        )
        oidc_wildcard = Principal(
            subject="oidc-user",
            tenant_id="default",
            roles=frozenset(),
            scopes=frozenset({"*"}),
            auth_method="oidc",
        )

        assert api_key_principal.can("screening:write") is True
        assert dev_principal.can("screening:delete") is True
        # Wildcard scope is restricted for standard OIDC auth unless explicitly allowed
        assert oidc_wildcard.can("screening:write") is False

    @pytest.mark.parametrize(
        ("role", "permission", "expected"),
        [
            ("recruiter", "screening:write", True),
            ("recruiter", "screening:read", True),
            ("recruiter", "screening:delete", False),
            ("service", "screening:write", True),
            ("reviewer", "screening:read", True),
            ("reviewer", "review:write", True),
            ("reviewer", "review:read", True),
            ("reviewer", "screening:delete", False),
            ("auditor", "audit:read", True),
            ("auditor", "screening:read", True),
            ("auditor", "review:read", True),
            ("auditor", "screening:write", False),
            ("privacy_officer", "screening:delete", True),
            ("privacy_officer", "screening:read", True),
            ("privacy_officer", "review:write", False),
            ("compliance_officer", "review:appeal", True),
            ("compliance_officer", "screening:write", False),
            ("model_evaluator", "evaluation:run", True),
            ("model_evaluator", "screening:delete", False),
        ],
    )
    def test_role_based_permissions_matrix(
        self, role: str, permission: str, expected: bool
    ) -> None:
        principal = Principal(
            subject="test-user",
            tenant_id="tenant-alpha",
            roles=frozenset({role}),
            scopes=frozenset(),
            auth_method="oidc",
        )
        assert principal.can(permission) is expected
