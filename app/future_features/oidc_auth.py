"""FUTURE FEATURE: enterprise OIDC/RBAC.

Implementation brief:
- Replace shared API keys with verified JWT access tokens from a configured issuer.
- Validate issuer, audience, expiry, signing keys, scopes, and role membership.
- Define separate permissions for submitting, viewing, deleting, reviewing, and administering.
- Add tenant isolation if the service becomes multi-tenant, and tests for cross-tenant data access.
- Do not implement JWT validation by decoding tokens without cryptographic verification.
"""
