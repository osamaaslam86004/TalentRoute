# Audit API Endpoint Reference

The `/api/v1/audit-events` endpoints provide access to tenant audit logs and execute cryptographic SHA-256 hash chain verification (`app.api.v1.endpoints.audit` and `app.audit.service`).

---

## Endpoint Summary

| HTTP Method | Path | Permission Required | Description |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/v1/audit-events/integrity` | `audit:read` | Execute cryptographic SHA-256 hash chain verification for tenant. |
| `GET` | `/api/v1/audit-events` | `audit:read` | List paginated audit event trail for tenant. |

---

## 1. Verify Audit Chain Integrity (`GET /api/v1/audit-events/integrity`)

Scans the tenant's complete audit event ledger sequentially to re-verify cryptographic hash link continuity.

- **HTTP Status Code:** `200 OK`
- **RBAC Roles Allowed:** `auditor`, `admin`
- **Audit Action Logged:** `audit.integrity_checked`

### Response Payload Schema (`AuditIntegrityRead`)

| Field Name | Type | Description |
| :--- | :--- | :--- |
| `tenant_id` | `string` | Authenticated tenant identifier. |
| `valid` | `boolean` | `true` if the cryptographic hash chain is unbroken and valid; `false` if tampering or sequence gaps are detected. |
| `event_count` | `integer` | Total number of audit events recorded for the tenant. |
| `head_sequence` | `integer` | Latest sequence number locked in `AuditChainHead`. |
| `first_failure_sequence` | `integer \| null` | Sequence number where integrity failure was detected (`null` if valid). |
| `failure_reason` | `string \| null` | Diagnostic code detailing cause of failure (`null` if valid). |
| `head_hash` | `string` | SHA-256 hash digest of the latest event in the chain. |

#### Response Payload Example (Valid Chain)
```json
{
  "tenant_id": "default",
  "valid": true,
  "event_count": 42,
  "head_sequence": 42,
  "first_failure_sequence": null,
  "failure_reason": null,
  "head_hash": "f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9"
}
```

---

## 2. List Audit Events (`GET /api/v1/audit-events`)

Retrieves paginated historical audit trail entries for the tenant.

- **HTTP Status Code:** `200 OK`
- **RBAC Roles Allowed:** `auditor`, `admin`
- **Audit Action Logged:** `audit.events_read`

### Query Parameters

| Parameter | Type | Default | Constraints | Description |
| :--- | :--- | :--- | :--- | :--- |
| `limit` | `integer` | `100` | Min 1, max 500 | Page size limit. |
| `offset` | `integer` | `0` | Min 0 | Pagination offset count. |
| `action` | `string \| null` | `None` | Max 120 chars | Optional filter by specific action name (e.g., `screening.created`, `review.feedback_submitted`). |

### Response Payload Schema (`AuditEventList`)

```json
{
  "items": [
    {
      "id": "d4e5f6a7-b8c9-0d1e-2f3a-4b5c6d7e8f9a",
      "tenant_id": "default",
      "sequence_no": 1,
      "occurred_at": "2026-11-02T12:00:00Z",
      "actor_subject": "recruiter-user-123",
      "actor_roles": ["recruiter"],
      "request_id": "c3a4f890-12ab-4cde-8901-23456789abcd",
      "action": "screening.created",
      "resource_type": "screening",
      "resource_id": "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d",
      "outcome": "success",
      "previous_hash": "0000000000000000000000000000000000000000000000000000000000000000",
      "event_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "event_metadata": {
        "status": "queued",
        "outbox": "pending"
      }
    }
  ],
  "limit": 100,
  "offset": 0,
  "count": 1
}
```

---

## Related Documents
- [API Reference Overview](overview.md)
- [Cryptographic Audit Chain Architecture](../architecture/audit-chain.md)
