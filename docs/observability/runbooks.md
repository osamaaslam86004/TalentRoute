# Operational Runbooks

This document provides step-by-step incident response runbooks for common production alerts and system issues.

---

## Runbook 1: Dead-Letter Outbox Accumulation (`OutboxDeadEventsSpike`)

### Symptom & Alerts
- Prometheus alert `OutboxDeadEventsSpike` firing.
- Metric `screening_pipeline_events_total{stage="outbox", outcome="dead"}` > 0.

### Diagnosis
1. Connect to PostgreSQL database and query dead events:
   ```sql
   SELECT id, tenant_id, attempt_count, last_error_code, created_at 
   FROM outbox_events 
   WHERE status = 'dead' 
   ORDER BY created_at DESC 
   LIMIT 10;
   ```
2. Common causes: Redis message broker outage, worker container crash loop, or invalid serialized task JSON.

### Remediation
1. Verify Celery workers and Redis broker are healthy (`docker compose ps` or Kubernetes pods).
2. Requeue dead-letter events using the operational CLI tool:
   ```bash
   # Option A: Requeue specific failing event ID
   python -m app.scripts.requeue_dead_outbox --event-id <EVENT_UUID>

   # Option B: Requeue batch of dead events after fixing underlying issue
   python -m app.scripts.requeue_dead_outbox --all-dead --limit 100
   ```

---

## Runbook 2: Audit Chain Integrity Verification Failure

### Symptom & Alerts
- Daily security compliance job or `GET /api/v1/audit-events/integrity` returns `"valid": false`.

### Diagnosis
1. Execute integrity check with verbose output to identify broken sequence number:
   ```bash
   curl -s -H "Authorization: Bearer $AUDITOR_TOKEN"      https://api.screening.example.com/api/v1/audit-events/integrity
   ```
2. Note `error_detail` and failing `sequence_no`.

### Remediation
1. **Security Incident Trigger:** A hash mismatch indicates manual row modification, table corruption, or unexpected sequence alteration.
2. Query the database row at the failing sequence number and compare `previous_hash` with `event_hash` of `sequence_no - 1`.
3. Export cryptographic chain state for security compliance review. Do **not** manually alter database rows to force agreement.

---

## Runbook 3: Database Connection Pool Saturation

### Symptom & Alerts
- High HTTP 500 error rates with logs displaying `TimeoutError: QueuePool limit of size 5 overflow 10 reached`.
- Endpoint latency spikes.

### Diagnosis
1. Check active database connection counts:
   ```sql
   SELECT count(*), state, query 
   FROM pg_stat_activity 
   WHERE datname = 'screening' 
   GROUP BY state, query;
   ```

### Remediation
1. Adjust database connection pool parameters in deployment environment:
   ```ini
   # Increase SQLAlchemy pool capacity for high async concurrency
   DB_POOL_SIZE=20
   DB_MAX_OVERFLOW=10
   ```
2. Scale up PostgreSQL database instance specs if connection limits are hit.

---

## Runbook 4: High HTTP 5xx Error Rates

### Symptom & Alerts
- Prometheus alert `High5xxErrorRate` firing (> 2% error rate over 5 minutes).

### Diagnosis
1. Inspect structured JSON logs filtering by level:
   ```bash
   kubectl logs -l app=screening-api --tail=100 | jq 'select(.level=="ERROR")'
   ```
2. Check external provider dependency status (OpenAI API outages or rate limit `429` responses).

### Remediation
1. If OpenAI API is failing, verify `OPENAI_API_KEY` validity and quota status.
2. Ensure rate-limiter fallback behavior (`RATE_LIMIT_FAIL_OPEN`) is configured appropriately.

---

## Related Documents
- [Outbox & Worker Architecture](../architecture/outbox-workers.md)
- [Audit Chain Architecture](../architecture/audit-chain.md)
- [Telemetry Reference](telemetry.md)
