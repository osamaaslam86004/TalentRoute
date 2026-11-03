# Transactional Outbox & Async Worker Architecture

The **Transactional Outbox Subsystem** (`app.workers.outbox`, `app.workers.celery_app`, `app.workers.tasks`) ensures reliable, idempotent background job execution without dual-write race conditions.

---

## The Dual-Write Problem & Outbox Solution

```
[ HTTP Client Request ]
         │
         ▼
[ FastAPI Endpoint ] ──► ( Single PostgreSQL Transaction )
                            ├── 1. INSERT INTO screenings (status='queued')
                            └── 2. INSERT INTO outbox_events (status='pending')
                                           │
                                           ▼ (Committed to DB)
                                  [ Celery Beat Poller ]
                                           │
                                           ▼ (Enqueue Task)
                                  [ Redis Broker ] ──► [ Celery Worker ]
```

---

## Outbox SQL Schema (`outbox_events`)

```sql
CREATE TABLE outbox_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(128) NOT NULL,
    event_type VARCHAR(80) NOT NULL,
    aggregate_id VARCHAR(128) NOT NULL,
    payload JSONB NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending', -- pending, dispatching, sent, dead
    attempt_count INT NOT NULL DEFAULT 0,
    available_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    locked_at TIMESTAMPTZ,
    dispatched_at TIMESTAMPTZ,
    last_error_code VARCHAR(80),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
```

---

## Outbox Dispatcher Lifecycle (`dispatch_pending_outbox`)

Celery Beat triggers `app.workers.outbox.dispatch_pending_outbox` every **10 seconds**:

1. **Stale Lease & Dead Event Cleanup:**
   - Outbox rows in `dispatching` state with `attempt_count >= OUTBOX_MAX_ATTEMPTS` (default 12) or expired lease (`locked_at < now - OUTBOX_LEASE_SECONDS`) are marked as `dead` with `last_error_code="dispatch_lease_expired"`.
2. **Atomic Batch Claiming (`_claim_batch`):**
   - Fetches up to `OUTBOX_BATCH_SIZE` (default 50) pending or stale dispatching events using `SELECT ... FOR UPDATE SKIP LOCKED`.
   - Sets `status = "dispatching"`, `locked_at = now`, and increments `attempt_count`.
3. **Task Enqueueing & W3C Trace Context Injection:**
   - Injects current OpenTelemetry trace context (`traceparent`, `tracestate`) into event payload headers.
   - Enqueues `screen_candidate` Celery task over Redis.
   - Updates outbox record status to `sent` and sets `dispatched_at = now`.

---

## Worker Task Processing (`screen_candidate`)

Celery workers execute `app.workers.tasks.screen_candidate`:

1. **Database Record Lock:** Retrieves screening row with `FOR UPDATE`.
2. **Reclaim Staleness Verification:** If another worker stalled, verifies `processing_started_at` exceeds `STALE_PROCESSING_SECONDS` (900s) before reclaiming.
3. **Execution Pipeline Stages:**
   - Stage `claim`: Updates row status to `processing`.
   - Stage `redact`: Applies PII minimization (`app.privacy.pii_redaction.redact_pii`).
   - Stage `graph`: Invokes compiled LangGraph assessment workflow.
   - Stage `persist`: Stores extracted assessments, sets status to `completed`, and appends SHA-256 audit event.
4. **Error Handling & Failure Recovery:**
   - If processing fails, updates row status to `failed`, records `error_code = "screening_processing_failed"`, logs audit event `screening.processing_failed`, and relies on Celery task retry backoff.

---

## Dead Outbox Event Recovery Script

Operators can requeue dead-letter outbox events using the administrative CLI tool:

```bash
# Requeue a single dead outbox event by UUID
python -m app.scripts.requeue_dead_outbox --event-id "b2c3d4e5-f6a7-8b9c-0d1e-2f3a4b5c6d7e"

# Requeue all dead outbox events (up to limit)
python -m app.scripts.requeue_dead_outbox --all-dead --limit 100
```

---

## Related Documents
- [System Architecture Overview](overview.md)
- [LangGraph Screening Pipeline Architecture](langgraph-pipeline.md)
- [Incident Runbooks](../observability/runbooks.md)
