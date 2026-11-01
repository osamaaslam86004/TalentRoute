# Telemetry & Observability Reference

The service features vendor-neutral OpenTelemetry instrumentation (`app.observability`), Prometheus RED metrics, trace context propagation, and strict privacy guardrails (`policy.py`).

---

## RED Metrics Reference

| Metric Name | Type | Unit | Description & Allowed Metric Labels |
| :--- | :--- | :--- | :--- |
| `http_requests_total` | Counter | Requests | Total HTTP requests handled. Labels: `route`, `method`, `status_class`. |
| `http_request_duration_seconds` | Histogram | Seconds | Endpoint execution latency. Labels: `route`, `method`, `status_class`. |
| `screening_pipeline_events_total` | Counter | Events | Workflow pipeline state transitions. Labels: `stage`, `outcome`, `status`. |
| `screening_pipeline_duration_seconds` | Histogram | Seconds | Total time to complete LangGraph processing. Labels: `stage`, `status`. |
| `auth_failures_total` | Counter | Failures | Authentication rejection counts. Labels: `reason` (`missing`, `invalid`, `forbidden`). |
| `outbox_dispatch_lag_seconds` | Histogram | Seconds | Delay from outbox creation to worker dispatch. |
| `pii_redactions_total` | Counter | Maskings | PII replacements made. Labels: `redaction_category`. |

---

## Telemetry Privacy & Cardinality Safeguards (`policy.py`)

To protect candidate PII and prevent metric cardinality explosions, all metric labels and span attributes pass through a strict allow-list policy filter.

### Allowed Metric Labels (`ALLOWED_METRIC_LABELS`)
`route`, `method`, `status_class`, `event_type`, `outcome`, `stage`, `model`, `operation`, `error_type`, `redaction_category`, `reason`, `decision`, `status`, `queue`.

### Forbidden Metric Labels (`FORBIDDEN_METRIC_LABELS`)
`tenant_id`, `actor`, `actor_hash`, `subject`, `screening_id`, `request_id`, `task_id`, `job_title`, `required_skills`, `path`, `url`, `application_text`.

!!! security "Zero-PII Telemetry Guarantee"
    Application text, prompt strings, candidate names, and tenant identifiers are **strictly prohibited** from metric labels. Actor subjects are anonymized using HMAC-SHA256 salt hashing (`actor_hash`).

---

## Structured JSON Logging vs. Console Logging

Configured via `LOG_FORMAT`:

=== "Console Format (`LOG_FORMAT=console`)"
    ```text
    2026-11-01 04:50:00 [INFO] app.workers.tasks: Processed screening_id=7f3c2b1a... status=processed
    ```

=== "JSON Format (`LOG_FORMAT=json`)"
    ```json
    {
      "timestamp": "2026-11-01T04:50:00.123456Z",
      "level": "INFO",
      "logger": "app.workers.tasks",
      "service": "candidate-screening",
      "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
      "span_id": "00f067aa0ba902b7",
      "tenant_id": "tenant-corp-alpha",
      "message": "Processed screening record successfully",
      "screening_id": "7f3c2b1a-8e9d-4c5b-a1b2-c3d4e5f6a7b8"
    }
    ```

---

## Distributed Tracing & W3C `traceparent`

- Trace contexts are propagated automatically across HTTP headers, outbox records, and Celery task execution using standard W3C `traceparent` headers.
- Allows end-to-end tracing from HTTP submission through outbox dispatch to background LLM worker completion in Jaeger/Grafana Tempo.

---

## Related Documents
- [Configuration Reference](../getting-started/configuration.md)
- [Operational Runbooks](runbooks.md)
