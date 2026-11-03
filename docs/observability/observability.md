# Observability & Operations Guide

This guide outlines the production telemetry stack, vendor-neutral OpenTelemetry instrumentation, metrics catalog, distributed tracing, alerting rules, and operational runbooks for the **LangGraph Candidate Screening Service**.

---

## Telemetry Architecture Overview

```
[ FastAPI App / Celery Workers ] ──► OpenTelemetry SDK (Tracing & RED Metrics)
                                             │
                                             ▼ (OTLP / HTTP Protobuf)
                                 [ OpenTelemetry Collector ]
                                             │
                                ┌────────────┴────────────┐
                                ▼                         ▼
                        [ Prometheus ]              [ Jaeger / OTLP ]
                                │
                                ▼
                       [ Grafana Dashboards ]
```

---

## Core Operational Pillars

1. **Vendor-Neutral RED Metrics:** HTTP request rate, errors, and latencies tracked with strict low-cardinality labels (`app.observability.metrics`).
2. **Distributed Tracing & Context Propagation:** W3C `traceparent` context propagated from API requests through outbox dispatch events into Celery background workers (`app.observability.tracing`).
3. **LLM Execution & Token Callbacks:** `TelemetryCallbackHandler` records LLM call duration, prompt tokens, completion tokens, and model versions without exporting applicant resume text to traces.
4. **Cryptographic Chain Monitoring:** Real-time tracking of audit chain validity (`app_audit_chain_valid`) and tenant database row lock contention (`app_audit_head_lock_duration_seconds`).

---

## Observability Documentation Catalog

- [Telemetry Catalog & OpenTelemetry Metrics](telemetry.md)
- [Operational Incident Runbooks](runbooks.md)
