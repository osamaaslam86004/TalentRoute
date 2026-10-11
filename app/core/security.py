"""Cryptographically verified OIDC authentication and coarse-grained RBAC."""

from __future__ import annotations

import asyncio
import hmac
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable

import jwt
from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient, PyJWKClientConnectionError, PyJWKClientError, PyJWTError

from app.core.config import Settings, get_settings

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_bearer = HTTPBearer(auto_error=False, scheme_name="OIDC Bearer Token")


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    tenant_id: str
    roles: frozenset[str]
    scopes: frozenset[str]
    auth_method: str

    def can(self, permission: str) -> bool:
        if "admin" in self.roles or permission in self.scopes:
            return True
        if self.auth_method in {"api_key", "development"} and "*" in self.scopes:
            return True
        accepted_roles = _PERMISSION_ROLES.get(permission, frozenset())
        return bool(self.roles.intersection(accepted_roles))


_PERMISSION_ROLES: dict[str, frozenset[str]] = {
    "screening:write": frozenset({"recruiter", "service"}),
    "screening:read": frozenset({"recruiter", "reviewer", "auditor", "privacy_officer"}),
    "screening:delete": frozenset({"privacy_officer"}),
    "review:write": frozenset({"reviewer"}),
    "review:read": frozenset({"reviewer", "auditor"}),
    "review:appeal": frozenset({"compliance_officer"}),
    "audit:read": frozenset({"auditor"}),
    "evaluation:run": frozenset({"model_evaluator"}),
}


@lru_cache(maxsize=8)
def _jwks_client(url: str) -> PyJWKClient:
    return PyJWKClient(url, cache_jwk_set=True, lifespan=3600, timeout=5)


def _claim_strings(value: object) -> frozenset[str]:
    if isinstance(value, str):
        return frozenset(part for part in value.replace(",", " ").split() if part)
    if isinstance(value, (list, tuple, set)):
        return frozenset(str(part).strip() for part in value if str(part).strip())
    return frozenset()


def _unauthorized(detail: str = "Authentication required") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _decode_oidc_token(token: str, settings: Settings) -> Principal:
    if not (settings.oidc_issuer and settings.oidc_audience and settings.oidc_jwks_url):
        raise HTTPException(status_code=503, detail="OIDC authentication is not configured")
    if len(token) > 32768:
        raise _unauthorized("Invalid access token")
    try:
        signing_key = _jwks_client(settings.oidc_jwks_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=settings.oidc_algorithms,
            audience=settings.oidc_audience,
            issuer=settings.oidc_issuer,
            options={"require": ["exp", "iat", "sub"]},
            leeway=15,
        )
    except PyJWKClientConnectionError as exc:
        raise HTTPException(status_code=503, detail="Identity provider signing keys are unavailable") from exc
    except (PyJWTError, PyJWKClientError) as exc:
        raise _unauthorized("Invalid or expired access token") from exc
    except Exception as exc:
        # Fail closed for unexpected validation failures without exposing token/provider details.
        raise _unauthorized("Unable to validate access token") from exc

    subject = claims.get("sub")
    tenant = claims.get(settings.oidc_tenant_claim)
    if not isinstance(subject, str) or not subject.strip():
        raise _unauthorized("Access token has no subject")
    if settings.oidc_require_tenant_claim and (not isinstance(tenant, str) or not tenant.strip()):
        raise HTTPException(status_code=403, detail="Access token is missing the tenant claim")
    if tenant and (len(str(tenant)) > 128 or any(ord(char) < 32 for char in str(tenant))):
        raise HTTPException(status_code=403, detail="Access token has an invalid tenant claim")

    roles = _claim_strings(claims.get(settings.oidc_roles_claim))
    raw_scopes = claims.get(settings.oidc_scopes_claim, claims.get("scp", claims.get("scope", "")))
    scopes = _claim_strings(raw_scopes)
    # Normalize common plural claim spellings without weakening action-level permissions.
    aliases = {"screenings:read": "screening:read", "screenings:write": "screening:write", "screenings:delete": "screening:delete"}
    scopes = frozenset(scopes.union(aliases[item] for item in scopes if item in aliases))
    return Principal(
        subject=subject,
        tenant_id=str(tenant).strip() if tenant else "default",
        roles=roles,
        scopes=scopes,
        auth_method="oidc",
    )


async def get_principal(
    request: Request,
    provided_key: str | None = Security(_api_key_header),
    bearer: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    settings = get_settings()
    if settings.auth_mode in {"auto", "oidc"} and bearer is not None:
        principal = await asyncio.to_thread(_decode_oidc_token, bearer.credentials, settings)
        request.state.actor_subject = principal.subject
        request.state.tenant_id = principal.tenant_id
        return principal

    if settings.auth_mode == "oidc":
        raise _unauthorized()

    expected = settings.api_key.get_secret_value() if settings.api_key else None
    if expected and provided_key and hmac.compare_digest(provided_key, expected):
        principal = Principal(
            subject="api-key-client",
            tenant_id="default",
            roles=frozenset({"admin"}),
            scopes=frozenset({"*"}),
            auth_method="api_key",
        )
        request.state.actor_subject = principal.subject
        request.state.tenant_id = principal.tenant_id
        return principal

    if settings.auth_mode == "auto" and expected is None and settings.environment in {"development", "test"}:
        principal = Principal(
            subject="local-development",
            tenant_id="default",
            roles=frozenset({"admin"}),
            scopes=frozenset({"*"}),
            auth_method="development",
        )
        request.state.actor_subject = principal.subject
        request.state.tenant_id = principal.tenant_id
        return principal

    raise _unauthorized("Invalid or missing credentials")


def require_permission(permission: str) -> Callable:
    async def dependency(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.can(permission):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return principal

    dependency.__name__ = f"require_{permission.replace(':', '_')}"
    return dependency
