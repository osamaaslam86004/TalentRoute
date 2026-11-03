# Cryptographic Audit Chain Architecture

The **Audit Subsystem** (`app.audit.service` and `app.audit.integrity`) maintains a per-tenant, tamper-evident SHA-256 cryptographic hash chain for regulatory compliance, non-repudiation, and audit verification.

---

## Cryptographic Hash Chain Structure

```
Genesis Event (sequence_no = 1, previous_hash = "0000...0000")
       │
       ▼  [SHA-256 canonical payload hash]
Event #1 (event_hash = "a1f8...")
       │
       ▼  (previous_hash = "a1f8...")
Event #2 (event_hash = "b2c9...")
       │
       ▼  (previous_hash = "b2c9...")
Event #3 (event_hash = "c3d0...") ──► Head Sequence locked in audit_chain_head table
```

---

## SQL Schema Definition (`audit_events` & `audit_chain_head`)

```sql
CREATE TABLE audit_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(128) NOT NULL,
    sequence_no BIGINT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    actor_subject VARCHAR(128) NOT NULL,
    actor_roles JSONB NOT NULL DEFAULT '[]'::jsonb,
    request_id VARCHAR(128),
    action VARCHAR(120) NOT NULL,
    resource_type VARCHAR(80) NOT NULL,
    resource_id VARCHAR(128),
    outcome VARCHAR(20) NOT NULL DEFAULT 'success',
    previous_hash VARCHAR(64) NOT NULL,
    event_hash VARCHAR(64) NOT NULL,
    event_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE audit_chain_head (
    tenant_id VARCHAR(128) PRIMARY KEY,
    last_sequence BIGINT NOT NULL,
    last_hash VARCHAR(64) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
```

---

## Canonical JSON & Hash Calculation Algorithm

To ensure byte-for-byte reproducibility across database platforms and languages, audit event payloads are formatted using deterministic canonical JSON (`app.audit.integrity.calculate_event_hash`):

1. **Canonical Schema Assembly (`audit_hash_payload`):**
   ```python
   {
       "id": str(event_id),
       "occurred_at": occurred_at.astimezone(UTC).isoformat(timespec="microseconds"),
       "tenant_id": tenant_id,
       "actor_subject": actor_subject,
       "actor_roles": sorted(actor_roles),
       "request_id": request_id,
       "action": action,
       "resource_type": resource_type,
       "resource_id": resource_id,
       "outcome": outcome,
       "metadata": metadata,
       "sequence_no": sequence_no,
       "previous_hash": previous_hash,
   }
   ```
2. **Canonical Serialization & Digesting:**
   - JSON encoded with `ensure_ascii=False`, `sort_keys=True`, `separators=(",", ":")`, `allow_nan=False`.
   - `event_hash = hashlib.sha256(canonical_bytes).hexdigest()`.

---

## Per-Tenant Serialization & Row Locking

When appending new audit events (`record_audit_event`), the database obtains an exclusive row lock on `audit_chain_head` using `SELECT ... FOR UPDATE`:
- Prevents race conditions and sequence gap anomalies during high-concurrency requests.
- First event for a tenant sets `previous_hash = "0" * 64` (Genesis Hash) and `sequence_no = 1`.
- Subsequent events set `previous_hash = head.last_hash` and `sequence_no = head.last_sequence + 1`.

---

## Chain Integrity Verification (`verify_audit_chain`)

The verification engine (`GET /api/v1/audit-events/integrity`) scans the tenant's complete event chain sequentially:

1. Asserts `sequence_no` starts at 1 and increases monotonically without gaps.
2. Asserts `event.previous_hash` matches the prior event's `event_hash`.
3. Re-calculates `calculate_event_hash(payload)` and compares with stored `event.event_hash`.
4. Asserts that the final event matches `AuditChainHead.last_sequence` and `AuditChainHead.last_hash`.

### Verification Failure Reasons
- `sequence_gap_or_reordering`: Sequence numbers are missing or out of order.
- `previous_hash_mismatch`: `previous_hash` pointer does not equal the preceding event's hash.
- `event_hash_mismatch`: Re-calculated payload SHA-256 digest differs from stored `event_hash`.
- `chain_head_mismatch`: `AuditChainHead` row differs from the last chain event.
- `chain_head_missing`: Audit events exist but no `AuditChainHead` row exists.

---

## Audit Event Action Catalog

| Action Name | Resource Type | Trigger Condition |
| :--- | :--- | :--- |
| `screening.created` | `screening` | Candidate screening submission accepted |
| `screening.idempotency_replayed` | `screening` | Duplicate submission deduplicated via Idempotency-Key |
| `screening.read` | `screening` | Single screening detail fetched by authorized principal |
| `screening.listed` | `screening_collection` | Screenings list fetched by authorized principal |
| `screening.deleted` | `screening` | Candidate screening record deleted (GDPR erasure) |
| `screening.processing_completed` | `screening` | Worker finished processing screening successfully |
| `screening.processing_failed` | `screening` | Worker encountered unrecoverable processing error |
| `review.feedback_submitted` | `reviewer_feedback` | Human reviewer submitted evaluation feedback |
| `review.feedback_listed` | `screening` | Human feedback history listed for a screening |
| `review.appeal_recorded` | `reviewer_feedback` | Compliance officer recorded an appeal determination |
| `audit.events_read` | `audit_event_collection` | Audit log entries queried |
| `audit.integrity_checked` | `audit_chain` | Cryptographic audit chain verification executed |
| `outbox.requeued` | `outbox_event` | Operator requeued dead-letter outbox event via CLI |

---

## Related Documents
- [Audit API Endpoint Reference](../api-reference/audit.md)
- [System Architecture Overview](overview.md)
- [Incident Runbooks](../observability/runbooks.md)
