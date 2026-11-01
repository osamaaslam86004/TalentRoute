# System Architecture Overview

The **LangGraph Candidate Screening Service** is an enterprise-grade, asynchronous backend service built with Python 3.12, FastAPI, PostgreSQL, Redis, Celery, and LangGraph.

---

## Core System Architecture

```mermaid
sequenceDiagram
    autonumber
    actor Client as API Client / HR Portal
    participant API as FastAPI Web Service
    participant DB as PostgreSQL Database
    participant Outbox as Outbox Dispatcher (Beat/Celery)
    participant Redis as Redis Broker
    participant Worker as Celery Screening Worker
    participant LLM as OpenAI (gpt-4o-mini)

    Client->>API: POST /api/v1/screenings (JWT / API Key)
    Note over API: Authenticate, Rate Limit & Sanitize Payload
    API->>DB: INSERT INTO screenings (status='pending')
    API->>DB: INSERT INTO outbox_events (event_type='screening.created')
    Note over API,DB: Single Atomic Database Transaction
    API-->>Client: HTTP 202 Accepted { id, status: 'pending' }

    loop Every 5 Seconds (Celery Beat)
        Outbox->>DB: SELECT FOR UPDATE SKIP LOCKED outbox_events (status='pending')
        Outbox->>Redis: LPUSH Celery Queue (screen_candidate task)
        Outbox->>DB: UPDATE outbox_events SET status='dispatching'
    end

    Redis->>Worker: Consume Task (screening_id)
    Worker->>DB: FETCH screening record
    Note over Worker: Execute PII Redaction Engine
    Worker->>LLM: Invoke LangGraph Workflow (Experience & Skills Extraction)
    LLM-->>Worker: Structured Pydantic Assessment & Confidence Scores
    Worker->>DB: UPDATE screenings SET status='processed', assessment data
    Worker->>DB: APPEND Audit Chain Event (SHA-256 Hash Chained)
```

---

## Key Architecture Components

### 1. Async FastAPI Front Door
- Fast HTTP ingestion layer handling payload validation, rate-limiting, and idempotency key checks.
- Returns immediate `202 Accepted` response with candidate screening ID.

### 2. Transactional Outbox Pattern
- Ensures atomic operations: candidate screening records and outbox event records are saved in the same PostgreSQL transaction.
- Eliminates dual-write anomalies (e.g. database save succeeds but message broker publish fails).

### 3. Redis Broker & Celery Workers
- Background task queues process long-running LLM workflow pipelines without blocking HTTP request threads.
- Celery Beat dispatches pending outbox events on a periodic schedule.

### 4. LangGraph Assessment Workflow
- Orchestrates multi-node LLM calls for structured experience categorization and skill matching.
- Enforces strict Pydantic output schemas, confidence scoring, and deterministic review routing.

### 5. Append-Only Hash-Chained Audit Log
- Maintains per-tenant SHA-256 tamper-evident event chains for regulatory compliance and audit readiness.

---

## Related Documents
- [LangGraph Pipeline Architecture](langgraph-pipeline.md)
- [Outbox & Worker Architecture](outbox-workers.md)
- [Audit Chain Architecture](audit-chain.md)
