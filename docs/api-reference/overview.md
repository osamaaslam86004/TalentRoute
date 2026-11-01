# API Reference Overview

The **LangGraph Candidate Screening API** exposes RESTful JSON endpoints under the base path `/api/v1`.

---

## Common Request Headers

| Header Name | Type | Required | Description | Example |
| :--- | :--- | :--- | :--- | :--- |
| `Authorization` | `string` | Conditional | Standard HTTP Bearer token (`Bearer <JWT>`). Required in OIDC mode. | `Bearer eyJhbGciOi...` |
| `X-API-Key` | `string` | Conditional | Constant-time API Key header. Alternative in `api_key` or `auto` mode. | `dev-only-change-me` |
| `X-Request-ID` | `string` | Optional | Client-supplied distributed tracing request correlation ID. | `c3a4f890-12ab-4cde-8901-23456789abcd` |
| `Idempotency-Key` | `string` | Optional | Unique submission deduplication key (8 to 128 characters: `^[A-Za-z0-9._:-]{8,128}$`). | `idemp-2026-11-01-001` |

---

## Error Response Format

All error responses adhere to standard FastAPI JSON detail objects:

```json
{
  "detail": "Application text exceeds configured limit"
}
```

### Standard HTTP Status Codes

| Code | Status Name | Description |
| :--- | :--- | :--- |
| `200 OK` | Successful Operation | Request processed successfully; payload returned. |
| `202 Accepted` | Async Job Accepted | Screening task accepted and enqueued for background worker processing. |
| `400 Bad Request` | Validation Error | Request body or query parameters failed Pydantic schema validation. |
| `401 Unauthorized` | Authentication Failed | Missing, expired, or cryptographically invalid access token / API key. |
| `403 Forbidden` | Access Denied | Authenticated principal lacks required RBAC permissions or tenant claims. |
| `404 Not Found` | Resource Missing | Requested screening ID or feedback record does not exist within tenant scope. |
| `409 Conflict` | Idempotency Re-use | `Idempotency-Key` reused with a conflicting payload fingerprint. |
| `413 Payload Too Large` | Limit Exceeded | `application_text` exceeds `MAX_APPLICATION_CHARS` limit. |
| `429 Too Many Requests` | Rate Limit Exceeded | Tenant request quota exceeded for the configured window. |
| `500 Internal Error` | Server Failure | Unexpected internal exception. Stack traces are scrubbed in response payloads. |

---

## Multi-Tenant Isolation

All data collections and audit records are strictly scoped to `tenant_id`:
- In **OIDC Mode**, `tenant_id` is extracted from the verified JWT claim.
- Queries automatically filter by `tenant_id = principal.tenant_id`.
- Principals cannot view, modify, or verify audit logs outside their assigned tenant context.

---

## Related Documents
- [Screenings Endpoint Reference](screenings.md)
- [Human Reviews API Reference](reviews.md)
- [Audit API Reference](audit.md)
