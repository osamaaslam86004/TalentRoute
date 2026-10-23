# Observability Guide

This document outlines the telemetry architecture, metrics, tracing, alerts, and operational runbooks for the candidate screening service.

## Signal Catalog & SLOs

- HTTP RED metrics (Rate, Errors, Duration)
  - `app_http_server_request_count_total`
  - `app_http_server_request_duration_seconds`
- Asynchronous screening pipeline telemetry & outbox metrics
  - `app_outbox_pending`, `app_outbox_dead`, `app_outbox_dispatch_lag_seconds`
  - `app_pipeline_events_total`
  - `app_screening_processing_duration_seconds`, `app_screening_end_to_end_duration_seconds`
- LLM latency and token counts via secure callbacks
  - Recorded via `TelemetryCallbackHandler` on LangChain graph executions
- Tamper-evident audit chain integrity monitoring
  - `app_audit_chain_valid`
  - `app_audit_head_lock_duration_seconds` (Audit head lock acquisition time for proactive tenant serialization bottleneck detection)

## Runbooks

### Requeuing Dead Outbox Events

When dead-letter events accumulate:

```bash
python -m app.scripts.requeue_dead_outbox --event-id <UUID>
```
