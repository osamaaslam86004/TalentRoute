# Authentication & RBAC Reference

This service enforces tenant-isolated, role-based access control (RBAC) using cryptographically verified OpenID Connect (OIDC) JWT bearer tokens, alongside an API key fallback mode (`app.auth.oidc_auth` and `app.core.security`).

---

## Authentication Modes (`AUTH_MODE`)

Authentication behavior is controlled by the `AUTH_MODE` configuration parameter:

=== "OIDC Mode (`oidc`)"
    - Enforces OIDC JWT validation for all request endpoints.
    - Token signatures are verified against remote public key sets fetched from `OIDC_JWKS_URL` using `PyJWKClient`.
    - API key headers are ignored.

=== "API Key Mode (`api_key`)"
    - Expects `X-API-Key` HTTP header.
    - Validates provided keys using constant-time string comparison (`hmac.compare_digest`).
    - Grants full `admin` role with wildcard scope (`*`).

=== "Auto Mode (`auto`)"
    - Checks for `Authorization: Bearer <token>` header first; validates via OIDC if present.
    - Falls back to `X-API-Key` header comparison if Bearer token is absent.
    - In `development` or `test` environments, if no key or token is configured, grants a dummy development principal (`subject="local-development"`, `roles={"admin"}`).

---

## OIDC JWT Verification Lifecycle

1. **JWKS Fetching & Caching:** `PyJWKClient` caches signing key sets for up to 1 hour (`lifespan=3600`) with a 5-second HTTP timeout.
2. **Claim Verification:** Validates `iss` (issuer), `aud` (audience), `exp` (expiration time), and asymmetric signature (`RS256`, etc.).
3. **Tenant Claim Verification:** Validates `tenant_id` claim (or custom claim specified by `OIDC_TENANT_CLAIM`).
   - If `OIDC_REQUIRE_TENANT_CLAIM=True` and tenant claim is missing or invalid, raises `403 Forbidden`.
   - Rejects tenant strings exceeding 128 characters or containing control characters.
4. **Subject Privacy Binding:** The raw token subject (`sub`) is attached to `request.state.actor_subject`. For telemetry, it is hashed using HMAC-SHA256 with `TELEMETRY_HASH_SALT` (`actor_hash`). Raw subjects are never emitted to logs or metric labels.

---

## Role-Based Access Control (RBAC)

Requests map to an internal `Principal` data object:

```python
@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    tenant_id: str
    roles: frozenset[str]
    scopes: frozenset[str]
    auth_method: str
```

### Permission Evaluation Flow (`principal.can(permission)`)
1. Returns `True` if `"admin"` is present in `roles` or `permission` exists in `scopes`.
2. Returns `True` if `auth_method` is `"api_key"` or `"development"` and `"*"` is in `scopes`.
3. Returns `True` if user roles intersect with explicit allowed roles defined in `_PERMISSION_ROLES`.

---

## RBAC Permission Matrix

| Permission Scope | Required Role(s) | Description | Target Endpoints |
| :--- | :--- | :--- | :--- |
| `screening:write` | `recruiter`, `service` | Submit candidate application for async screening | `POST /api/v1/screenings` |
| `screening:read` | `recruiter`, `reviewer`, `auditor`, `privacy_officer` | List or view screening results and status | `GET /api/v1/screenings`, `GET /api/v1/screenings/{id}` |
| `screening:delete` | `privacy_officer` | Hard delete screening records (GDPR/CCPA erasure) | `DELETE /api/v1/screenings/{id}` |
| `review:write` | `reviewer` | Submit human reviewer feedback and label decisions | `POST /api/v1/screenings/{id}/feedback` |
| `review:read` | `reviewer`, `auditor` | View human feedback history and ratings | `GET /api/v1/screenings/{id}/feedback` |
| `review:appeal` | `compliance_officer` | Append appeal determinations to human review feedback | `POST /api/v1/reviews/{feedback_id}/appeals` |
| `audit:read` | `auditor` | Inspect audit event log and verify cryptographic hash chain | `GET /api/v1/audit-events`, `GET /api/v1/audit-events/integrity` |
| `evaluation:run` | `model_evaluator` | Execute offline accuracy and calibration evaluation pipelines | CLI evaluation tooling |

---

## Related Documents
- [Configuration Reference](configuration.md)
- [API Reference Overview](../api-reference/overview.md)
