# Operational Incident Runbooks

This document provides actionable procedures for resolving common production alerts and operational incidents.

---

## Runbook 1: Outbox Dead-Letter Event Accumulation

### Trigger Condition
Alert `OutboxDeadEventsAccumulating` fires when metric `app_outbox_dead > 0`.

### Diagnostic Steps
1. Inspect dead-letter outbox records in PostgreSQL:
   ```sql
   SELECT id, tenant_id, event_type, aggregate_id, attempt_count, last_error_code, created_at
   FROM outbox_events
   WHERE status = 'dead'
   ORDER BY created_at DESC;
   ```
2. Check Celery worker and Redis broker connectivity logs to identify the root cause (e.g. Redis timeout, Celery worker loss, or database lock contention).

### Remediation Action
Once the underlying infrastructure issue is resolved, use the administrative CLI to requeue dead events:

```bash
# Requeue a specific dead event by UUID
python -m app.scripts.requeue_dead_outbox --event-id "b2c3d4e5-f6a7-8b9c-0d1e-2f3a4b5c6d7e"

# Requeue all dead-letter events (up to limit 100)
python -m app.scripts.requeue_dead_outbox --all-dead --limit 100
```

---

## Runbook 2: Audit Hash Chain Integrity Verification Failure

### Trigger Condition
Alert `AuditChainIntegrityFailure` fires when metric `app_audit_chain_valid == 0` or API call `GET /api/v1/audit-events/integrity` returns `valid: false`.

### Diagnostic Steps
1. Execute the integrity verification endpoint to pinpoint the failure sequence number and diagnostic reason:
   ```bash
   curl -X GET "http://localhost:8000/api/v1/audit-events/integrity"      -H "Authorization: Bearer <AUDITOR_JWT_TOKEN>"
   ```
2. Review failure reason in JSON response:
   - `sequence_gap_or_reordering`: Sequence numbers skip or are misaligned. Check for manual row deletion or direct SQL modifications.
   - `previous_hash_mismatch`: An event's `previous_hash` does not match the preceding row's `event_hash`.
   - `event_hash_mismatch`: Event payload data was modified post-insertion.
   - `chain_head_mismatch`: `audit_chain_head` row is out of sync with the latest `audit_events` row.

### Remediation Action
1. Freeze write access for the affected tenant if unauthorized SQL modification is suspected.
2. Inspect database binary logs / WAL records to identify unauthorized `UPDATE` or `DELETE` statements executed on `audit_events`.
3. Audit chain entries are immutable; manual SQL updates to restore hash links must be documented and signed by the compliance team.

---

## Runbook 3: High Submission Rate Limiting (HTTP 429 Spikes)

### Trigger Condition
Alert `HighSubmissionRateLimiting` fires when `app_rate_limit_decisions_total{decision="limited"}` rate increases rapidly.

### Diagnostic Steps
1. Identify submitting tenant and actor hash from API access logs:
   ```bash
   grep "rate_limit_exceeded" /var/log/candidate-screening/api.log
   ```
2. Verify whether the request surge is legitimate batch submission activity or an uncoordinated API client retry loop.

### Remediation Action
- If legitimate traffic surge, increase submission limit settings in environment configuration:
  ```ini
  SCREENING_RATE_LIMIT_REQUESTS=100
  SCREENING_RATE_LIMIT_WINDOW_SECONDS=60
  ```
- If Redis is unresponsive, check `RATE_LIMIT_FAIL_OPEN` setting to determine fail-closed vs fail-open behavior.

---

## Runbook 4: LangGraph LLM Pipeline Processing Failures

### Trigger Condition
Alert `PipelineFailureRateHigh` fires when `app_pipeline_events_total{outcome="failure"}` rate exceeds 5% of submissions.

### Diagnostic Steps
1. Inspect failed screening records in PostgreSQL:
   ```sql
   SELECT id, tenant_id, status, error_code, attempt_count, updated_at
   FROM screenings
   WHERE status = 'failed'
   ORDER BY updated_at DESC;
   ```
2. Check OpenAI provider API error logs (such as HTTP 429 rate limits or HTTP 503 provider outages).

### Remediation Action
- Verify `OPENAI_API_KEY` validity and quota status.
- Adjust `LLM_TIMEOUT_SECONDS` (default 45.0s) or `TASK_MAX_RETRIES` (default 2) if provider latencies are elevated.

---

## Related Documents
- [Observability & Operations Guide](observability.md)
- [Telemetry & Metrics Catalog](telemetry.md)
