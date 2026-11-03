# OpenTelemetry Metrics & Tracing Reference

This reference details the low-cardinality OpenTelemetry metrics catalog, distributed tracing spans, and privacy policy safeguards (`app.observability.metrics` and `app.observability.tracing`).

---

## Prometheus RED & Pipeline Metric Catalog

| Metric Identifier | Instrument Type | Unit | Key Labels | Description |
| :--- | :--- | :--- | :--- | :--- |
| `app_http_server_request_count_total` | Counter | Requests | `route`, `method`, `status_code` | HTTP request volume across API routes. Status codes grouped into low-cardinality strings. |
| `app_http_server_request_duration_seconds` | Histogram | Seconds | `route`, `method`, `status_code` | HTTP request latency histogram distribution. |
| `app_auth_failures_total` | Counter | Failures | `reason` (`missing`, `invalid`, `forbidden`, `jwks_unavailable`) | Total authentication and authorization failure occurrences. |
| `app_rate_limit_decisions_total` | Counter | Decisions | `decision` (`allowed`, `limited`, `backend_error_closed`, `backend_error_open`) | Rate limiter decision outcomes. |
| `app_pipeline_events_total` | Counter | Events | `event_type`, `outcome`, `stage`, `status` | Screening pipeline state transition counter. |
| `app_screening_processing_duration_seconds` | Histogram | Seconds | None | Worker execution duration for processing a single screening. |
| `app_screening_end_to_end_duration_seconds` | Histogram | Seconds | None | Total elapsed time from initial HTTP submission (`POST`) to worker completion. |
| `app_outbox_pending` | Gauge | Count | None | Number of pending outbox events awaiting dispatch. |
| `app_outbox_dead` | Gauge | Count | None | Number of dead-letter outbox events requiring operator intervention. |
| `app_outbox_dispatch_lag_seconds` | Histogram | Seconds | None | Time lag between outbox event creation and successful dispatch. |
| `app_redis_queue_length` | Gauge | Count | None | Number of enqueued tasks in the Redis task queue. |
| `app_audit_chain_valid` | Gauge | Boolean | None | Audit hash chain status (`1` = valid, `0` = broken/tampered). |
| `app_audit_head_lock_duration_seconds` | Histogram | Seconds | `outcome` (`success`, `failure`) | Time spent acquiring tenant row lock on `audit_chain_head`. |
| `app_db_pool_in_use` | Gauge | Connections | None | Active PostgreSQL database connection count. |
| `app_db_pool_available` | Gauge | Connections | None | Idle/available PostgreSQL database connection count. |

---

## Distributed Tracing & Execution Stages

The service propagates W3C Trace Context headers (`traceparent`, `tracestate`) across asynchronous boundaries:

```
[ HTTP POST /screenings ] (Span: HTTP POST /api/v1/screenings)
          │
          ▼ (Store Outbox Row with Trace Context)
[ Outbox Dispatcher ] (Span: outbox.dispatch)
          │
          ▼ (Celery Message Header Propagation)
[ Celery Worker ] (Span: celery.process_screening)
          ├── Stage "claim": DB Row Lock & Processing State
          ├── Stage "redact": Pre-Provider PII Minimization
          ├── Stage "graph": LangGraph Execution (Nodes: categorize_experience, assess_skillset)
          └── Stage "persist": Result Storage & Audit Chain Append
```

---

## Privacy & Telemetry Guard Policies

1. **Zero-PII Metric Labels:** Metric labels use strictly enumerated, low-cardinality strings. Candidate text, names, emails, and raw subjects are **never** placed in metric labels or span attributes.
2. **Actor Anonymization (`actor_hash`):** Raw user subjects are hashed with `TELEMETRY_HASH_SALT` into a 16-character hex digest before being attached to span context.
3. **LangSmith Guard Policy (`enforce_langsmith_policy`):** In `staging` or `production`, startup **fails immediately** if LangSmith prompt-tracing environment variables are detected when `ALLOW_LANGSMITH_TRACING=False`.

---

## Related Documents
- [Observability Guide](observability.md)
- [Operational Incident Runbooks](runbooks.md)
