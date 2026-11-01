# Append-Only Audit Chain Architecture

To fulfill compliance, privacy, and legal auditability standards, the service maintains a **tenant-isolated, tamper-evident SHA-256 hash-chained audit log** (`app.audit`).

---

## Cryptographic Hash-Chain Mechanics

Each audit event record is linked cryptographically to its predecessor:

```
Genesis Event (sequence_no=1)
├── previous_hash: 0000000000000000000000000000000000000000000000000000000000000000 (GENESIS_HASH)
└── event_hash: SHA256(Canonical JSON Payload 1)
        │
        ▼
Audit Event 2 (sequence_no=2)
├── previous_hash: event_hash of Event 1
└── event_hash: SHA256(Canonical JSON Payload 2)
        │
        ▼
Audit Event 3 (sequence_no=3)
├── previous_hash: event_hash of Event 2
└── event_hash: SHA256(Canonical JSON Payload 3)
```

---

## Canonical Payload Hashing (`calculate_event_hash`)

The event hash is computed by canonicalizing the payload dictionary into deterministic UTF-8 JSON bytes before taking the SHA-256 digest:

```python
canonical = json.dumps(
    payload,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
).encode("utf-8")
event_hash = hashlib.sha256(canonical).hexdigest()
```

---

## Concurrency Control & Row Locking (`AuditChainHead`)

To guarantee strict sequential ordering (`sequence_no`) without cryptographic forks during high concurrent traffic, audit creation locks the tenant's chain head row:

```sql
-- Step 1: Ensure chain head exists
INSERT INTO audit_chain_heads (tenant_id, last_sequence, last_hash, updated_at)
VALUES (:tenant_id, 0, :genesis_hash, CURRENT_TIMESTAMP)
ON CONFLICT (tenant_id) DO NOTHING;

-- Step 2: Lock chain head for tenant
SELECT * FROM audit_chain_heads 
WHERE tenant_id = :tenant_id 
FOR UPDATE;
```

---

## Metadata Projection Allow-list

To prevent sensitive PII, prompts, or arbitrary data from polluting the audit chain, metadata dictionary keys are strictly validated against `_ALLOWED_METADATA`:

```python
_ALLOWED_METADATA = {
    "status", "count", "limit", "offset", "prompt_version", 
    "model_version", "rubric_version", "label", "reason_codes", 
    "redacted_categories", "latency_ms", "cost_usd", "dataset_version", 
    "report_id", "appeal_outcome", "attempt_count", "record_type", "schema_valid"
}
```

- **Value Limits:** String values are capped at 160 characters; list items capped at 20 elements of 80 characters. Unallowed keys are discarded automatically.

---

## Cryptographic Chain Verification

The chain integrity verification endpoint (`GET /api/v1/audit-events/integrity`) scans all events sequentially for the authenticated tenant:

1. Re-calculates expected SHA-256 hash for every historical event payload.
2. Asserts that `event.previous_hash` matches the prior event's `event_hash`.
3. Returns `valid: true` if unbroken, or details the sequence number of any detected tampering.

---

## Related Documents
- [Audit API Reference](../api-reference/audit.md)
- [Operational Runbooks](../observability/runbooks.md)
