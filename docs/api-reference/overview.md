# API Reference Overview

The **LangGraph Candidate Screening API** exposes RESTful JSON endpoints under the base URL path `/api/v1`.

---

## Common Request Headers

| Header Name | Data Type | Required | Constraints | Description |
| :--- | :--- | :--- | :--- | :--- |
| `Authorization` | `string` | Conditional | `Bearer <JWT>` | Standard HTTP Bearer access token header. Required when `AUTH_MODE=oidc` or in `auto` mode. |
| `X-API-Key` | `string` | Conditional | Secret string | Constant-time API Key header. Required when `AUTH_MODE=api_key` or in `auto` fallback. |
| `X-Request-ID` | `string` | Optional | String | Client-supplied distributed correlation ID for request tracing. If omitted, the service generates a UUID. |
| `Idempotency-Key` | `string` | Optional | Min 8, max 128 chars (`^[A-Za-z0-9._:-]{8,128}$`) | Client-supplied key for submission deduplication. Idempotency keys are scoped to the authenticated tenant. |

---

## Response Headers

| Header Name | Description | Example |
| :--- | :--- | :--- |
| `X-Request-ID` | Unique request correlation UUID returned on all HTTP responses for distributed tracing. | `c3a4f890-12ab-4cde-8901-23456789abcd` |
| `Retry-After` | Returned on `429` (Rate Limited) or `503` (limiter unavailable) indicating wait time in seconds. | `60` |

---

## Error Response Format & Security Scrubbing

All endpoint errors return standardized FastAPI JSON payloads:

```json
{
  "detail": "Application text exceeds configured limit",
  "request_id": "c3a4f890-12ab-4cde-8901-23456789abcd"
}
```

!!! security "Exception & Stack Scrubbing Security Guarantee"
    Unhandled server exceptions (`500 Internal Server Error`) are caught by `unhandled_exception_handler` (`app.main`). Internal exception details, tracebacks, database schema specifics, prompts, or candidate resume data are **never** reflected in response payloads.

---

## Complete HTTP Status Code Matrix

| Status Code | Code Name | Description | Response Model |
| :--- | :--- | :--- | :--- |
| `200 OK` | Successful Operation | Request processed successfully; resource data returned. | Endpoint specific |
| `201 Created` | Resource Created | Human feedback or appeal record created successfully. | `ReviewerFeedbackRead` |
| `202 Accepted` | Async Task Enqueued | Candidate screening application accepted and queued for worker execution. | `ScreeningAccepted` |
| `204 No Content` | Deleted | Screening record hard deleted successfully. | Empty body |
| `400 Bad Request` | Validation Error | Request body or parameters failed Pydantic schema validation. | `{"detail": "..."}` |
| `401 Unauthorized` | Authentication Failed | Missing, expired, or cryptographically invalid access token or API key. | `{"detail": "..."}` |
| `403 Forbidden` | Access Denied | Authenticated principal lacks required RBAC role/scope or tenant claim. | `{"detail": "..."}` |
| `404 Not Found` | Resource Missing | Target screening ID or feedback record does not exist for tenant. | `{"detail": "..."}` |
| `409 Conflict` | Idempotency Key Conflict | Reused `Idempotency-Key` with a different request payload fingerprint. | `{"detail": "..."}` |
| `413 Payload Too Large` | Character Limit Exceeded | `application_text` character count exceeds `MAX_APPLICATION_CHARS`. | `{"detail": "..."}` |
| `429 Too Many Requests` | Submission Rate Limited | Tenant request volume exceeded configured window quota. | `{"detail": "..."}` |
| `500 Internal Error` | Unhandled Error | Unexpected server error. Detailed stack trace is scrubbed. | `{"detail": "Internal server error"}` |
| `503 Service Unavailable` | Backend Unavailable | OIDC identity provider or rate limiter Redis backend unreachable. | `{"detail": "..."}` |

---

## Multi-Tenant Scope Isolation

- Data collections (`screenings`, `reviewer_feedback`, `audit_events`) are strictly partitioned by `tenant_id`.
- Tenant context is bound automatically from OIDC claims or fallback defaults (`request.state.tenant_id`).
- Endpoints enforce tenant boundaries; requests cannot access or alter resources belonging to other tenants.

---

## Related Documents
- [Screenings API Endpoint Reference](screenings.md)
- [Reviewer Feedback API Reference](reviews.md)
- [Audit API Endpoint Reference](audit.md)
