"""Verified OIDC/JWT helpers used by FastAPI security dependencies.

Signature, algorithm, issuer, audience, expiry and required claims are validated with PyJWT and a
cached JWKS client. Role/scope authorization is exposed by `app.core.security.require_permission`.
"""

from app.core.config import Settings, get_settings
from app.core.security import Principal, _decode_oidc_token


def decode_access_token(token: str, settings: Settings | None = None) -> Principal:
    """Verify an OIDC access token and map trusted claims into an internal principal."""

    return _decode_oidc_token(token, settings or get_settings())


__all__ = ["Principal", "decode_access_token"]
