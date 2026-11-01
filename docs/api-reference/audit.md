# Audit Events API Reference

The `/api/v1/audit-events` resource exposes endpoints for compliance auditing and cryptographic hash-chain verification.

---

## 1. List Audit Events (`GET /api/v1/audit-events`)

Retrieves paginated, tenant-isolated audit log events.

- **Permission Required:** `audit:read` (`auditor` role)
- **Query Parameters:**
  - `limit` (`int`, default `100`, min `1`, max `500`)
  - `offset` (`int`, default `0`, min `0`)
  - `action` (`str | None`, max length `120`, e.g. `screening.created`, `review.feedback_submitted`)

### Response Payload (`AuditEventList`)
```json
{
  "items": [
    {
      "id": "11223344-5566-7788-9900-aabbccddeeff",
      "sequence_no": 42,
      "occurred_at": "2026-11-01T04:50:00.125000Z",
      "tenant_id": "tenant-corp-alpha",
      "actor_subject": "recruiter-user-101",
      "actor_roles": ["recruiter"],
      "request_id": "req-9988776655",
      "action": "screening.created",
      "resource_type": "screening",
      "resource_id": "7f3c2b1a-8e9d-4c5b-a1b2-c3d4e5f6a7b8",
      "outcome": "success",
      "metadata": {
        "status": "pending",
        "prompt_version": "screening-prompts-v1.1.0"
      },
      "previous_hash": "3f7a1b8c...",
      "event_hash": "8e9d0c1b..."
    }
  ],
  "limit": 100,
  "offset": 0,
  "count": 42
}
```

---

## 2. Verify Audit Chain Integrity (`GET /api/v1/audit-events/integrity`)

Cryptographically verifies the SHA-256 hash chain sequence for the tenant.

- **Permission Required:** `audit:read` (`auditor` role)

### Successful Response (`AuditIntegrityRead` - Unbroken Chain)
```json
{
  "valid": true,
  "event_count": 42,
  "last_sequence": 42,
  "last_hash": "8e9d0c1b...",
  "checked_at": "2026-11-01T05:15:00.000000Z",
  "error_detail": null
}
```

### Failure Response (Tamper Detected)
```json
{
  "valid": false,
  "event_count": 42,
  "last_sequence": 18,
  "last_hash": "1a2b3c4d...",
  "checked_at": "2026-11-01T05:15:00.000000Z",
  "error_detail": "Hash mismatch at sequence 19: stored previous_hash does not match sequence 18 event_hash."
}
```

---

## Related Documents
- [Audit Chain Architecture](../architecture/audit-chain.md)
- [Operational Runbooks](../observability/runbooks.md)
