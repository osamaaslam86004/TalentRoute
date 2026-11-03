# LangGraph Candidate Screening Service (v1.2.0 / v1.0.0 Release Specs)

An enterprise-grade, asynchronous FastAPI service that transforms candidate resume screening into a structured, auditable, and evidence-focused **decision-support** workflow. Built with LangGraph, SQLAlchemy 2.0 Async, Celery, and Redis, this system features tenant-scoped OIDC/RBAC security, privacy-safe SHA-256 hash-chained audit trails, pre-provider PII redaction, append-only human review and appeal ledgers, request idempotency protection via PostgreSQL transactional outbox, and an offline evaluation engine with blinded pairwise review tooling.

> **Mandatory AI Compliance & Safety Notice:**  
> **Human review is strictly mandatory.** The Candidate Screening Service provides advisory, evidence-grounded decision-support materials intended solely for qualified human reviewers and talent acquisition personnel. The service **never** automatically rejects applicants, makes final hiring decisions, schedules candidate interviews, or sends automated candidate communications. Outputs must not be used as the sole basis for employment decisions under applicable labor and data privacy regulations.

---

## Architecture Overview

The system uses a decoupled, event-driven architecture designed for multi-tenant isolation, linear horizontal scaling, zero dual-write vulnerabilities, and end-to-end cryptographic auditability.

```text
                                +---------------------------------------+
                                |  Client (HTTP API Consumer / UI)      |
                                +---------------------------------------+
                                                    |
                         OIDC Bearer Token (preferred) or X-API-Key Fallback
                                                    v
                                +---------------------------------------+
                                |  FastAPI / RBAC / Tenant Boundary     |
                                +---------------------------------------+
                                                    |
            +---------------------------------------+---------------------------------------+
            |                                       |                                       |
            v                                       v                                       v
+-----------------------+               +-----------------------+               +-----------------------+
|  PostgreSQL Database  |               |     Redis Service     |               | Celery Beat Scheduler |
|  - Screenings         |               |  - Shared Rate Limit  |               +-----------------------+
|  - Hash-Chained Audit |               |  - Celery Broker      |                           |
|  - Review Feedback    |               +-----------------------+                           |
|  - Transaction Outbox |                           ^                                       v
+-----------------------+                           |                           +-----------------------+
            ^                                       +---------------------------| Outbox Dispatcher     |
            |                                                                   +-----------------------+
            |                                                                               |
            +-------------------------------------------------------------------------------+
                                                    v
                                        +-----------------------+
                                        | Celery Worker Node    |
                                        |  - PII Minimization   |
                                        |  - LangGraph Pipeline |
                                        |  - Audit Event Logging|
                                        +-----------------------+
                                                    |
                                                    v
                                        +-----------------------+
                                        |  LLM Provider (OpenAI)|
                                        +-----------------------+
```

### Subsystem Architecture & Responsibilities

* **FastAPI REST Layer & Pydantic v2:** Fully asynchronous web server supporting OpenAPI 3.0 schema generation, strict payload validation, tenant-scoped authentication, and rate-limiting guardrails.
* **PostgreSQL & Async SQLAlchemy 2.0:** Relational persistence engine maintaining database schemas, strict foreign-key integrity, and version-controlled database migrations via Alembic (`0001_initial`, `0002_enterprise_features`, `0003_outbox_idempotency`).
* **Redis Broker & Shared Rate Limiter:** High-performance in-memory cache supporting shared sliding-window Lua rate limiting across API nodes and serving as the underlying transport for Celery task message queues.
* **Celery Worker Nodes:** Distributed background workers executing asynchronous candidate assessments, pre-provider text redaction, LangGraph execution, and audit trail record generation.
* **Celery Beat Scheduler & Transactional Outbox:** Outbox event dispatcher daemon querying pending `outbox_events` rows within PostgreSQL, acquiring row-level locks (`locked_at`), enqueuing tasks to Celery, and handling lease recovery for stale processes.
* **Pre-Provider PII Minimization Engine:** Automated regex-based redaction engine filtering candidate contact info, government IDs, and addresses prior to external LLM provider API dispatch while retaining original application text in isolated PostgreSQL database storage for human review.
* **Tamper-Evident SHA-256 Audit Ledger:** Per-tenant append-only event trail where every audit event calculates a cryptographic SHA-256 digest linking the current event metadata to the preceding event hash (`previous_hash`). Database triggers block row updates and deletions to guarantee immutability.

---

## Quick Start & Docker Compose Guide

### Prerequisites
* Docker Engine 24.0+ & Docker Compose v2.20+
* Python 3.11+ (for local environment setups)
* Valid OpenAI API key (or compatible LLM provider credentials)

### 1. Environment Setup
Create a local runtime configuration file from the provided template:

```bash
cp .env.example .env
```

Key environment variables to configure in `.env`:

```bash
# Core Application & Storage Settings
ENVIRONMENT=development
OPENAI_API_KEY=sk-proj-your-api-key-here
POSTGRES_PASSWORD=postgres
REDIS_PASSWORD=redis

# Authentication & Rate Limiting
AUTH_MODE=auto
API_KEY=dev-only-change-me
RATE_LIMIT_SCREENINGS_PER_MINUTE=60

# PII Redaction Engine Settings
PII_REDACTION_ENABLED=true
PII_REDACTION_POLICY=baseline
```

### 2. Launch Stack with Docker Compose
Start the core application stack (PostgreSQL, Redis, FastAPI API, Celery Worker, Celery Beat Dispatcher):

```bash
docker compose up --build -d
```

Stream logs across the API server, worker nodes, and Beat scheduler:

```bash
docker compose logs -f api worker beat
```

### 3. Launch Stack with Full Observability
Spin up the service alongside the OpenTelemetry Collector, Prometheus, and Grafana:

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml up --build -d
```

Access local observability web interfaces:
* **Prometheus Dashboard:** `http://localhost:9090`
* **Grafana Dashboards:** `http://localhost:3000` (Default login: `admin` / `admin`)
* **Interactive API Docs (Swagger UI):** `http://localhost:8000/docs` (Disabled when `ENVIRONMENT=production`)

---

## Local Development & Makefile Workflows

### Virtual Environment Setup & Database Migrations

```bash
# 1. Create and activate virtual environment
python3.11 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\Activate.ps1

# 2. Install package in editable mode with development dependencies
make install

# 3. Apply database migrations
make migrate

# 4. Launch FastAPI development web server
make run
```

### Makefile Target Reference

| Target | Command Executed | Description |
| :--- | :--- | :--- |
| `make install` | `python -m pip install -e '.[dev]'` | Install package in editable mode with dev dependencies |
| `make test` | `pytest -q` | Run Pytest test suite quietly |
| `make lint` | `ruff check app tests migrations` | Execute Ruff linter and static analysis checks |
| `make run` | `uvicorn app.main:app --reload` | Run FastAPI development server with live reload |
| `make worker` | `celery -A app.workers.celery_app:celery_app worker --loglevel=INFO` | Launch background Celery worker node |
| `make migrate` | `alembic upgrade head` | Apply all pending database schema migrations |
| `make up` | `docker compose up --build -d` | Build and launch core application containers |
| `make down` | `docker compose down` | Tear down core application containers |
| `make logs` | `docker compose logs -f api worker` | Stream live container logs for API and worker |
| `make obs-up` | `docker compose -f docker-compose.yml -f docker-compose.observability.yml up --build -d` | Launch stack with OTel Collector, Prometheus, and Grafana |
| `make obs-down` | `docker compose -f docker-compose.yml -f docker-compose.observability.yml down` | Tear down full observability stack |

---

## Authentication, Security & RBAC Matrix

The service implements multi-tenant security controls configured via `AUTH_MODE`:

* **`oidc` (Production Standard):** Validates RSA/ECDSA signed JWT bearer tokens against identity provider public keys retrieved via a cached JWKS client (`PyJWKClient`). Enforces `iss`, `aud`, `exp`, `iat`, and custom tenant claims (`tenant_id`). HTTPS transport is strictly required in production.
* **`api_key`:** Inspects incoming `X-API-Key` headers using constant-time string comparison (`hmac.compare_digest`). Designed for single-tenant internal microservice integrations.
* **`auto` (Default):** Accepts valid OIDC bearer tokens when configured, falling back to `X-API-Key`. In `development` or `test` modes, unauthenticated requests default to a local admin principal.

### Authentication Configuration Example

```bash
AUTH_MODE=auto
API_KEY=dev-only-change-me
OIDC_ISSUER=https://identity.example.com/realms/candidate-screening
OIDC_AUDIENCE=candidate-screening-api
OIDC_JWKS_URL=https://identity.example.com/realms/candidate-screening/protocol/openid-connect/certs
OIDC_ROLES_CLAIM=roles
OIDC_SCOPES_CLAIM=scope
OIDC_TENANT_CLAIM=tenant_id
OIDC_REQUIRE_TENANT_CLAIM=true
OIDC_ALGORITHMS=RS256
```

### Role-Based Access Control (RBAC) Permission Matrix

| Permission | Default Granted Roles | Purpose & Operational Scope |
| :--- | :--- | :--- |
| `screening:write` | `recruiter`, `service` | Submit new candidate applications for automated screening |
| `screening:read` | `recruiter`, `reviewer`, `auditor`, `privacy_officer` | List and inspect tenant-isolated screening results |
| `screening:delete` | `privacy_officer` | Permanently erase candidate application text (GDPR right-to-be-forgotten) |
| `review:write` | `reviewer` | Submit structured human review feedback on model output |
| `review:read` | `reviewer`, `auditor` | Inspect historical reviewer feedback and appeal chains |
| `review:appeal` | `compliance_officer` | Append formal decision appeal outcomes to existing review feedback |
| `audit:read` | `auditor` | Query audit log events and verify cryptographic SHA-256 chain integrity |
| `evaluation:run` | `model_evaluator` | Run offline evaluation benchmarks and pairwise model comparisons |

*(Note: Authenticated principals holding the `admin` role or matching OAuth2 scopes bypass role-level restrictions within their assigned tenant boundary.)*

---

## Complete API Endpoint Reference

All business routes mandate authentication, enforce tenant isolation boundaries, and return standardized JSON error responses.

### 1. Health & Readiness Probes

#### Liveness Probe
`GET /health/live`
* **Auth Required:** None
* **Status Codes:** `200 OK`
* **Response Body Example:**
```json
{
  "status": "alive"
}
```

#### Readiness Probe
`GET /health/ready`
* **Auth Required:** None
* **Status Codes:** `200 OK` (when dependencies are healthy), `503 Service Unavailable` (if DB/Redis ping fails)
* **Response Body Example:**
```json
{
  "status": "ready",
  "database": "ok",
  "redis": "ok"
}
```

---

### 2. Candidate Screenings Management & GDPR Erasure

#### Submit Candidate Application
`POST /api/v1/screenings`
* **Permission Required:** `screening:write`
* **Header Options:** `Idempotency-Key` (Optional, 8-128 chars, alphanumeric and `._:-`)
* **Status Codes:** `202 Accepted`, `409 Conflict` (Idempotency key re-used with different payload), `413 Payload Too Large` (Exceeds `MAX_APPLICATION_CHARS`), `429 Too Many Requests`

##### Request Body Example
```json
{
  "application_text": "Senior Software Engineer with 8 years of experience building distributed systems in Python and Go. Led migration to Kubernetes and PostgreSQL.",
  "job_title": "Senior Backend Engineer",
  "required_skills": ["Python", "PostgreSQL", "Kubernetes", "Distributed Systems"]
}
```

##### Response Body Example (`202 Accepted`)
```json
{
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "queued",
  "poll_url": "http://localhost:8000/api/v1/screenings/3fa85f64-5717-4562-b3fc-2c963f66afa6"
}
```

#### List Screenings
`GET /api/v1/screenings?limit=50&offset=0`
* **Permission Required:** `screening:read`
* **Status Codes:** `200 OK`

#### Get Screening Details
`GET /api/v1/screenings/{screening_id}`
* **Permission Required:** `screening:read`
* **Status Codes:** `200 OK`, `404 Not Found`

##### Response Body Example (`200 OK`)
```json
{
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "tenant_id": "default",
  "status": "completed",
  "job_title": "Senior Backend Engineer",
  "required_skills": ["Python", "PostgreSQL", "Kubernetes"],
  "model_version": "gpt-4o-mini",
  "prompt_version": "screening-prompts-v1.1.0",
  "rubric_version": "v1.0.0",
  "pii_redacted": true,
  "pii_policy": "baseline",
  "human_review_required": true,
  "overall_recommendation": "hr_review",
  "overall_score": 0.88,
  "overall_reasoning": "Strong match for backend core competencies. Recommended for technical recruiter phone screen.",
  "experience_assessment": {
    "years_relevant_experience": 8.0,
    "seniority_level": "Senior",
    "key_achievements": ["Led Kubernetes migration", "Architected PostgreSQL distributed systems"]
  },
  "skills_assessment": {
    "matched_skills": ["Python", "PostgreSQL", "Kubernetes"],
    "missing_skills": [],
    "skill_match_ratio": 1.0
  },
  "created_at": "2026-11-03T01:00:00Z",
  "updated_at": "2026-11-03T01:00:05Z"
}
```

#### Delete Screening Data (GDPR Right-to-be-Forgotten)
`DELETE /api/v1/screenings/{screening_id}`
* **Permission Required:** `screening:delete`
* **Status Codes:** `204 No Content`, `404 Not Found`
* **Behavior:** Permanently deletes the candidate application record. Detaches related reviewer feedback records (`screening_id` set to `NULL`) while maintaining immutable audit event logs for compliance transparency.

---

### 3. Reviewer Feedback & Append-Only Appeal Ledgers

#### Submit Reviewer Feedback
`POST /api/v1/screenings/{screening_id}/feedback`
* **Permission Required:** `review:write`
* **Allowed Label Enum:** `agree`, `disagree`, `insufficient_evidence`, `override_recommendation`
* **Allowed Reason Codes Enum:** `evidence_missing`, `evidence_conflicts`, `role_requirement_mismatch`, `redaction_review_needed`, `policy_or_accessibility_concern`, `rubric_ambiguity`, `other`
* **Status Codes:** `201 Created`, `404 Not Found`, `422 Unprocessable Entity`

##### Request Body Example
```json
{
  "label": "disagree",
  "reason_codes": ["evidence_missing", "role_requirement_mismatch"],
  "notes": "Candidate displays strong Python background, but lacks verified experience with required async frameworks."
}
```

#### List Review History
`GET /api/v1/screenings/{screening_id}/feedback?limit=50&offset=0`
* **Permission Required:** `review:read`
* **Status Codes:** `200 OK`, `404 Not Found`

#### Append Appeal Outcome
`POST /api/v1/reviews/{feedback_id}/appeals`
* **Permission Required:** `review:appeal`
* **Allowed Outcome Enum:** `pending`, `upheld`, `revised`
* **Status Codes:** `201 Created`, `404 Not Found`
* **Immutability Architecture:** Appends a new immutable ledger record (`record_type="appeal"`, `parent_feedback_id=<feedback_id>`). Original model predictions and reviewer notes remain unmodified.

##### Request Body Example
```json
{
  "outcome": "revised",
  "reason_codes": ["evidence_conflicts"],
  "notes": "Appeal review confirmed additional candidate repository links provided in supplemental application."
}
```

---

### 4. Audit Trail & Cryptographic Chain Verification

#### List Audit Events
`GET /api/v1/audit-events?limit=100&offset=0&action=screening.created`
* **Permission Required:** `audit:read`
* **Status Codes:** `200 OK`

#### Verify Cryptographic Audit Chain Integrity
`GET /api/v1/audit-events/integrity`
* **Permission Required:** `audit:read`
* **Status Codes:** `200 OK`
* **Response Body Example:**
```json
{
  "tenant_id": "default",
  "valid": true,
  "event_count": 142,
  "head_sequence": 142,
  "first_failure_sequence": null,
  "failure_reason": null,
  "head_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
}
```

---

## PII Minimization Engine

To protect candidate privacy and satisfy strict data minimization requirements, raw application text passes through an automated regex redaction engine before payload transmission to third-party LLM providers.

### Dual-Storage Architecture
1. **Isolated Database Storage:** Original raw candidate text is stored in PostgreSQL, accessible only to authorized human reviewers with `screening:read` permissions.
2. **Provider Payload Redaction:** Sanitized text with masked identifiers is sent to the LLM inference provider.

### Redaction Configuration & Policy Rules

```bash
PII_REDACTION_ENABLED=true
PII_REDACTION_POLICY=baseline  # Options: 'baseline' or 'strict'
```

* **`baseline` (Default):** Redacts explicit, structured identifiers:
  * Email Addresses (`[REDACTED_EMAIL]`)
  * Phone Numbers (`[REDACTED_PHONE]`)
  * Government / Tax / Social Security IDs (`[REDACTED_ID]`)
  * Labelled Applicant Names (`[REDACTED_NAME]`)
  * Labelled Home & Street Addresses (`[REDACTED_ADDRESS]`)
* **`strict`:** Applies all `baseline` rules plus heuristic unlabelled street address pattern detection.

---

## Operational & Evaluation CLI Tooling

The repository provides administrative and offline model evaluation utilities located under `app/scripts/`.

### 1. Offline Benchmark Evaluation
Runs offline evaluation against versioned golden datasets without making live network requests:

```bash
python -m app.scripts.evaluate_offline \
  --dataset datasets/evaluation_v1.jsonl \
  --predictions predictions/candidate_model.jsonl \
  --model-version gpt-4o-mini \
  --prompt-version screening-prompts-v1.1.0 \
  --output reports/candidate-eval.json
```

### 2. Release Gate Comparison
Evaluates candidate model performance against a baseline report across strict release thresholds (exits with code `0` on pass, `2` on gate failure):

```bash
python -m app.scripts.compare_evaluations \
  --baseline reports/baseline-eval.json \
  --candidate reports/candidate-eval.json \
  --max-accuracy-drop 0.03 \
  --max-schema-drop 0.00 \
  --max-slice-accuracy-gap 0.20 \
  --max-distribution-shift 0.25 \
  --max-latency-increase 0.25 \
  --max-cost-increase 0.25 \
  --output reports/release-gate-report.json
```

### 3. Create Blinded Pairwise Review Tasks
Generates anonymized A/B review tasks for human evaluation while outputting a restricted identity mapping file:

```bash
python -m app.scripts.create_pairwise_review \
  --candidate-a predictions/model_a.jsonl \
  --candidate-b predictions/model_b.jsonl \
  --candidate-a-version model-v1 \
  --candidate-b-version model-v2 \
  --public-output reports/blinded-review-tasks.json \
  --private-map-output .private/blinded-review-map.json
```
*(Note: The identity mapping file in `.private/` is generated with restricted `0o600` permissions and automatically ignored by `.gitignore`.)*

### 4. Score Blinded Pairwise Judgments
Unblinds completed reviewer judgments using the private map file and calculates model win rates:

```bash
python -m app.scripts.score_pairwise_review \
  --public-review reports/completed-blinded-review.json \
  --private-map .private/blinded-review-map.json \
  --output reports/pairwise-summary.json
```

### 5. Requeue Dead-Letter Outbox Events
Operator utility for manually recovering dead-letter outbox events after resolving systemic failures:

```bash
# Requeue a specific dead-letter event by ID
python -m app.scripts.requeue_dead_outbox --event-id 3fa85f64-5717-4562-b3fc-2c963f66afa6

# Batch requeue dead-letter events (up to limit)
python -m app.scripts.requeue_dead_outbox --all-dead --limit 100
```

---

## Observability, Telemetry & Production Hardening

### OpenTelemetry Metrics & Distributed Tracing
The application exports OpenTelemetry spans and RED metrics (Rate, Errors, Duration) to the OpenTelemetry Collector (`make obs-up`).

### Privacy-Safe Telemetry Guardrails
* **Zero PII & Text in Spans:** Candidate application text, LLM prompts, model completions, secret tokens, and reviewer notes are strictly excluded from telemetry metadata, log lines, and trace attributes.
* **Keyed Actor Hashing:** User subjects and tenant identifiers are hashed with `TELEMETRY_HASH_SALT` prior to metric emission.

### Prometheus Alerting Rules
Pre-configured alert triggers defined in `deploy/observability/alerts/screening-alerts.yml`:
* `HighOutboxDeadLetterCount`: Triggers when dead-letter outbox event count exceeds 0.
* `AuditChainIntegrityFailure`: Triggers when `app_audit_chain_valid == 0`.
* `HighRateLimitDropRate`: Triggers when client rate-limiting drop rate spikes.
* `HighAPIErrorRate`: Triggers when HTTP 5xx server error rate exceeds 5%.

### Production Hardening Checklist

- [ ] **Identity Provider Configuration:** Deploy a production OIDC provider (Keycloak, Auth0, Okta) and enforce HTTPS for issuer and JWKS metadata URLs.
- [ ] **Secret Manager Integration:** Inject sensitive environment variables (`OPENAI_API_KEY`, `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `TELEMETRY_HASH_SALT`, `API_KEY`) via enterprise key vaults.
- [ ] **Database Least-Privilege Access:** Grant application DB roles `SELECT`, `INSERT`, and `UPDATE` privileges on `screenings`, `reviewer_feedback`, `outbox_events`, and `audit_chain_heads`. Restrict `DELETE` and `TRUNCATE` access.
- [ ] **Network & Transport Isolation:** Enforce TLS 1.3 termination at the ingress proxy and isolate Redis and PostgreSQL inside private VPC subnets.
- [ ] **Disk & Backup Encryption:** Enable transparent data encryption (TDE) for PostgreSQL database storage volumes and point-in-time backups.
- [ ] **Observability & Incident Alerting:** Route Prometheus alerts to PagerDuty or Slack notification channels.
- [ ] **Legal & AI Governance Review:** Conduct algorithmic bias and fairness reviews with legal counsel to maintain compliance with federal, state, and regional hiring laws.
