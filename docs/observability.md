# Observability Guide

This document outlines the telemetry architecture, metrics, tracing, alerts, and operational runbooks for the candidate screening service.

## Signal Catalog & SLOs

- HTTP RED metrics (Rate, Errors, Duration)
- Asynchronous screening pipeline telemetry & outbox metrics
- LLM latency and token counts via secure callbacks
- Tamper-evident audit chain integrity monitoring

## Runbooks

### Requeuing Dead Outbox Events

When dead-letter events accumulate:

```bash
python -m app.scripts.requeue_dead_outbox --event-id <UUID>
```
