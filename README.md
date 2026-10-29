# LangGraph Candidate Screening Service

An asynchronous, enterprise-grade FastAPI service that transforms candidate resume screening into a structured, auditable, evidence-focused **decision-support** workflow. This implementation adds tenant-scoped OIDC/RBAC authentication, privacy-safe audit trails, pre-provider PII minimization, append-only human-review and appeal records, prompt/model provenance tracking, and an offline evaluation pipeline with blinded pairwise review tooling.

> **Mandatory Compliance & Safety Notice:**  
> **Human review is strictly mandatory.** The service never automatically rejects applicants, makes a hiring decision, schedules interviews, or sends candidate communications. Outputs are advisory decision-support materials designed solely for qualified human reviewers. Do not use this output as the sole basis for employment decisions.

---

## Architecture Overview

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

### Core Architecture Components

* **FastAPI & Pydantic v2:** Fully async RESTful API endpoints with strict input normalization, request validation, and schema enforcement.
* **PostgreSQL / SQLAlchemy 2.0 Async / Alembic:** Durable persistence layer with async connection pooling, strict relational integrity, and versioned Alembic schema migrations (`0001_initial`, `0002_enterprise_features`, `0003_outbox_idempotency`).
* **Redis & Celery Outbox Worker:** Asynchronous background assessment processing utilizing a PostgreSQL transactional outbox pattern to eliminate dual-write risks. Includes automatic lease recovery, exponential backoff retries, dead-letter queueing, and a dedicated Celery Beat outbox dispatcher.
* **Submission Controls & Idempotency:** Tenant/actor-scoped Redis Lua rate limiting, request fingerprinting, `Idempotency-Key` replay protection, and stale-worker claim recovery.
* **LangGraph & LangChain Pipeline:** Structured evidence extraction and skill matching workflows with structured Pydantic outputs (`ExperienceAssessment`, `SkillsAssessment`). All workflows route candidates to a mandatory human-review queue (`hr_review`, `recruiter_review`, or `manual_review`).
* **OIDC/JWT & RBAC Security:** Cryptographic token verification using a cached JWKS client (`PyJWKClient`), issuer/audience/expiration validation, tenant claim enforcement, constant-time API key fallback (`hmac.compare_digest`), and fine-grained action permissions.
* **Tamper-Evident Audit Trail:** Append-only, per-tenant sequence numbers linked by SHA-256 hash chains. Protected by PostgreSQL database triggers preventing row updates or deletions, allow-listed metadata projection, and cryptographic chain verification endpoints (`/api/v1/audit-events/integrity`).
* **PII Minimization Engine:** Configurable pre-provider regex redaction engine (`baseline` vs. `strict` policies) masking emails, phone numbers, government IDs, and labelled names/addresses prior to provider dispatch while retaining raw text in isolated database storage for authorized human review.
* **Offline Evaluation & Release Pipeline:** Synthetic starter evaluation datasets, exact label agreement metrics with bootstrap 95% confidence intervals, role-family slice analysis, lexical evidence grounding proxies, confidence calibration diagnostics (ECE, Brier score), conservative release gate comparisons, and blinded A/B review generation tools.
* **Full Observability Stack:** Vendor-neutral OpenTelemetry instrumentation (RED metrics, trace context propagation), structured JSON logging with privacy guardrails, Prometheus alert rules (`screening-alerts.yml`), and Grafana monitoring dashboards.

---

## Quick Start with Docker Compose

### Prerequisites
* Docker Engine 24.0+ & Docker Compose v2.20+
* An active OpenAI API key (or compatible LLM provider)

### 1. Environment Setup
Copy the example environment file and configure required secrets:

```bash
cp .env.example .env
```

Edit `.env` to configure your runtime credentials:
* Set `OPENAI_API_KEY` to your provider key.
* Update `API_KEY`, `POSTGRES_PASSWORD`, and `REDIS_PASSWORD` for private development stacks. Never deploy example credentials to shared environments.

### 2. Launch the Application Stack
Start the core services (PostgreSQL, Redis, FastAPI API, Celery Worker, Celery Beat):

```bash
docker compose up --build -d
```

View application logs in real-time:

```bash
docker compose logs -f api worker beat
```

### 3. Launch with Complete Observability
To spin up the service alongside the OpenTelemetry Collector, Prometheus, and Grafana:

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml up --build -d
```

### 4. Interactive API Documentation
In `development` or `test` mode, interactive OpenAPI documentation is available at:
* Swagger UI: `http://localhost:8000/docs`
* OpenAPI Schema: `http://localhost:8000/openapi.json`

*(Note: API documentation endpoints are automatically disabled when `ENVIRONMENT=production`.)*

---

## Local Development & Makefile Workflows

### Prerequisites
* Python 3.11+
* Local PostgreSQL instance (or Docker container)
* Local Redis instance (or Docker container)

### Setup & Local Execution

```bash
# 1. Create and activate a Python virtual environment
python3.11 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\Activate.ps1

# 2. Install package in editable mode with development dependencies
make install

# 3. Configure local environment variables
cp .env.example .env

# 4. Apply Alembic database migrations
make migrate

# 5. Start the FastAPI development server
make run

# 6. In a second terminal, start the Celery worker
make worker

# 7. In a third terminal, start the Celery Beat outbox dispatcher
celery -A app.workers.celery_app:celery_app beat --loglevel=INFO
```

### Makefile Reference

| Target | Command Executed | Description |
| :--- | :--- | :--- |
| `make install` | `python -m pip install -e '.[dev]'` | Install package in editable mode with dev dependencies |
| `make test` | `pytest -q` | Run pytest test suite quietly |
| `make lint` | `ruff check app tests migrations` | Run Ruff linter and static code analysis |
| `make run` | `uvicorn app.main:app --reload` | Run FastAPI server with auto-reload |
| `make worker` | `celery -A app.workers.celery_app:celery_app worker --loglevel=INFO` | Run Celery worker node |
| `make migrate` | `alembic upgrade head` | Apply latest Alembic database migrations |
| `make up` | `docker compose up --build -d` | Build and start core Docker Compose stack |
| `make down` | `docker compose down` | Stop and remove core Docker Compose containers |
| `make logs` | `docker compose logs -f api worker` | Stream logs for API and worker containers |
| `make obs-up` | `docker compose -f docker-compose.yml -f docker-compose.observability.yml up --build -d` | Launch stack with OTel Collector, Prometheus & Grafana |
| `make obs-down` | `docker compose -f docker-compose.yml -f docker-compose.observability.yml down` | Stop and remove full observability stack |

---

## Authentication, Authorization & Security Controls

Authentication is configured via `AUTH_MODE` in `.env`:

* **`oidc` (Preferred for Production):** Cryptographically verifies RSA/ECDSA signed JWTs against identity provider public keys fetched via a cached JWKS client (`PyJWKClient`). Validates `iss` (issuer), `aud` (audience), `exp` (expiration), `iat` (issued at), and tenant claim rules. In `production`, issuer and JWKS URLs must use HTTPS.
* **`api_key`:** Validates incoming requests using constant-time string comparison (`hmac.compare_digest`) against `X-API-Key` headers. Suitable for single-tenant internal service integrations.
* **`auto` (Default):** Accepts cryptographically verified OIDC bearer tokens when OIDC settings are present; falls back to `API_KEY` when configured. Unauthenticated requests in `development` or `test` environments fall back to a local admin principal.

### Configuration Parameters

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

| Permission | Default Role Membership | Purpose & Scope |
| :--- | :--- | :--- |
| `screening:write` | `recruiter`, `service` | Submit new screening applications |
| `screening:read` | `recruiter`, `reviewer`, `auditor`, `privacy_officer` | List and read tenant-scoped candidate screening records |
| `screening:delete` | `privacy_officer` | Delete screening application records (data erasure) |
| `review:write` | `reviewer` | Submit reviewer feedback records |
| `review:read` | `reviewer`, `auditor` | Read reviewer feedback and appeal histories |
| `review:appeal` | `compliance_officer` | Append appeal outcome records |
| `audit:read` | `auditor` | Read tenant-scoped audit event logs and chain integrity |
| `evaluation:run` | `model_evaluator` | Run offline evaluation pipeline benchmarks |

*(Note: Principals possessing the `admin` role or explicit matching OAuth2 scopes bypass role checks within their authenticated tenant.)*

---

## API Endpoint Reference

All business endpoints require authentication and enforce multi-tenant data boundaries.

### 1. Health & Readiness Probes

#### Liveness Probe
`GET /health/live`
* **Status Codes:** `200 OK`
* **Response Body:** `{"status": "alive"}`

#### Readiness Probe
`GET /health/ready`
* **Checks:** Performs active query probes against PostgreSQL (`SELECT 1`) and Redis (`ping()`).
* **Status Codes:** `200 OK` (when all probes pass) or `503 Service Unavailable` (when any component fails).
* **Response Body:** `{"status": "ready", "database": "ok", "redis": "ok"}`

---

### 2. Screening Submissions & Management

#### Submit Candidate Screening
`POST /api/v1/screenings`
* **Permission Required:** `screening:write`
* **Headers:** Optional `Idempotency-Key` (8–128 characters matching `^[A-Za-z0-9._:-]{8,128}$`).
* **Rate Limiting:** Governed by `SCREENING_RATE_LIMIT_REQUESTS` (default 30) per `SCREENING_RATE_LIMIT_WINDOW_SECONDS` (default 60s). Fails closed (`503 Service Unavailable`) on Redis outages unless `RATE_LIMIT_FAIL_OPEN=true`.
* **Idempotency Rules:**
  * Replaying an identical request payload with the same `Idempotency-Key` returns the cached `202 Accepted` response.
  * Reusing an `Idempotency-Key` with a modified payload returns `409 Conflict`.
* **Status Codes:** `202 Accepted` (Enqueued), `409 Conflict` (Key reuse mismatch), `413 Payload Too Large` (Exceeds `MAX_APPLICATION_CHARS`), `429 Too Many Requests`.

##### Request Payload Example
```json
{
  "application_text": "Five years building Python backend APIs with FastAPI, PostgreSQL, AsyncIO, and Docker. Led microservice architecture and security hardening.",
  "job_title": "Senior Python Engineer",
  "required_skills": ["Python", "FastAPI", "PostgreSQL", "Docker", "AsyncIO"]
}
```

##### Response Payload Example (`202 Accepted`)
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
* **Response Body:** Paginated array of tenant-scoped screening summaries (`items`, `limit`, `offset`, `count`).

#### Get Screening Detail
`GET /api/v1/screenings/{screening_id}`
* **Permission Required:** `screening:read`
* **Status Codes:** `200 OK`, `404 Not Found` (if missing or owned by another tenant).
* **Response Summary:** Returns assessment status, job title, required skills, model/prompt versions, structured evidence, and recommendation routing. Raw application text is intentionally omitted from read payloads.

#### Delete Screening Data
`DELETE /api/v1/screenings/{screening_id}`
* **Permission Required:** `screening:delete`
* **Status Codes:** `204 No Content`, `404 Not Found`
* **Behavior:** Permanently deletes the candidate application row. Detaches associated reviewer feedback (`screening_id` set to `NULL` via foreign key `ON DELETE SET NULL`) while preserving audit logs.

---

### 3. Reviewer Feedback & Appeals

#### Submit Reviewer Feedback
`POST /api/v1/screenings/{screening_id}/feedback`
* **Permission Required:** `review:write`
* **Allowed Labels:** `agree`, `disagree`, `insufficient_evidence`, `override_recommendation`
* **Allowed Reason Codes:** `evidence_missing`, `evidence_conflicts`, `role_requirement_mismatch`, `redaction_review_needed`, `policy_or_accessibility_concern`, `rubric_ambiguity`, `other`
* **Status Codes:** `201 Created`, `404 Not Found`, `422 Unprocessable Entity`

##### Request Payload Example
```json
{
  "label": "disagree",
  "reason_codes": ["evidence_missing", "role_requirement_mismatch"],
  "notes": "Candidate work history shows Python experience, but lacks required async architecture evidence."
}
```

#### List Review History
`GET /api/v1/screenings/{screening_id}/feedback?limit=50&offset=0`
* **Permission Required:** `review:read`
* **Status Codes:** `200 OK`, `404 Not Found`

#### Append Appeal Outcome
`POST /api/v1/reviews/{feedback_id}/appeals`
* **Permission Required:** `review:appeal`
* **Allowed Outcomes:** `pending`, `upheld`, `revised`
* **Status Codes:** `201 Created`, `404 Not Found`
* **Immutability Principle:** Creates a new linked record (`record_type="appeal"`, `parent_feedback_id=<feedback_id>`). Original reviewer feedback records and model outputs are immutable and cannot be overwritten.

---

### 4. Audit Trail & Integrity Verification

#### List Audit Events
`GET /api/v1/audit-events?limit=100&offset=0&action=screening.created`
* **Permission Required:** `audit:read`
* **Status Codes:** `200 OK`

#### Verify Audit Chain Integrity
`GET /api/v1/audit-events/integrity`
* **Permission Required:** `audit:read`
* **Status Codes:** `200 OK`
* **Response Payload Example:**
```json
{
  "tenant_id": "default",
  "valid": true,
  "event_count": 42,
  "head_sequence": 42,
  "first_failure_sequence": null,
  "failure_reason": null,
  "head_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
}
```

---

## PII Minimization Engine

To protect candidate privacy, application text is passed through an automated regex-based PII minimization engine prior to sending data to external LLM providers. Original raw application text remains safely stored in PostgreSQL for authorized human reviewers.

### Configuration
```bash
PII_REDACTION_ENABLED=true
PII_REDACTION_POLICY=baseline  # Options: 'baseline' or 'strict'
```

### Redaction Policies

* **`baseline` (Default):** Redacts structured PII identifiers:
  * Emails (`[REDACTED_EMAIL]`)
  * Phone numbers (`[REDACTED_PHONE]`)
  * Government/Tax/Employee IDs (`[REDACTED_ID]`)
  * Labelled applicant names (`[REDACTED_NAME]`)
  * Labelled home/street addresses (`[REDACTED_ADDRESS]`)
* **`strict`:** Applies all baseline rules plus unlabelled street address detection (e.g., `123 Main Street, Suite 400`).

---

## Operational & Evaluation CLI Scripts

The `app/scripts/` directory provides operational and offline model evaluation utilities.

### 1. Offline Model Evaluation
Scores saved candidate predictions JSONL files against a versioned human-gold dataset without making provider network calls:

```bash
python -m app.scripts.evaluate_offline \
  --dataset datasets/evaluation_v1.jsonl \
  --predictions predictions/candidate_model.jsonl \
  --model-version gpt-4o-mini \
  --prompt-version screening-prompts-v1.1.0 \
  --output reports/candidate-eval.json
```

### 2. Conservative Release Gate Comparison
Compares baseline and candidate evaluation reports against release thresholds (returns exit code `0` on pass, `2` on gate failure):

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
Generates anonymized A/B review tasks for human reviewers while creating a restricted identity map:

```bash
python -m app.scripts.create_pairwise_review \
  --candidate-a predictions/model_a.jsonl \
  --candidate-b predictions/model_b.jsonl \
  --candidate-a-version model-v1 \
  --candidate-b-version model-v2 \
  --public-output reports/blinded-review-tasks.json \
  --private-map-output .private/blinded-review-map.json
```

*(Note: The private identity map file is created with restricted `0o600` file permissions and excluded from version control.)*

### 4. Score Blinded Pairwise Judgments
Unblinds completed reviewer judgments using the private identity map and aggregates preference win rates:

```bash
python -m app.scripts.score_pairwise_review \
  --public-review reports/completed-blinded-review.json \
  --private-map .private/blinded-review-map.json \
  --output reports/pairwise-summary.json
```

### 5. Requeue Dead-Letter Outbox Events
Operator utility for safely requeuing dead-letter outbox events after resolving systemic failures:

```bash
# Requeue a single dead-letter event by UUID
python -m app.scripts.requeue_dead_outbox --event-id 3fa85f64-5717-4562-b3fc-2c963f66afa6

# Batch requeue dead-letter events (up to limit)
python -m app.scripts.requeue_dead_outbox --all-dead --limit 100
```

---

## Database Migrations & Outbox Worker Mechanics

### Alembic Schema Migrations
Database migrations are managed via Alembic:

```bash
# Upgrade database to latest schema
alembic upgrade head
```

* **`0001_initial`:** Creates core `screenings` table.
* **`0002_enterprise_features`:** Adds tenant scoping, model/prompt provenance fields, `reviewer_feedback`, `audit_events`, and `audit_chain_heads`.
* **`0003_outbox_idempotency`:** Adds request fingerprinting, idempotency keys, and `outbox_events`.

### Transactional Outbox Pattern
When a screening is submitted, the business record (`screenings`), audit event (`audit_events`), and transactional outbox row (`outbox_events`) commit within the same PostgreSQL transaction.

1. **Dispatch:** A dedicated Celery Beat task (`dispatch_pending_outbox`) queries pending outbox records and enqueues worker tasks (`screen_candidate`).
2. **Lease Recovery:** Claimed outbox rows acquire a lease lock (`locked_at`). Stale leases from crashed dispatchers are automatically recovered.
3. **Dead-Letter Handling:** Events failing repeatedly increment `attempt_count`. Upon reaching `OUTBOX_MAX_ATTEMPTS` (default 12), the event transitions to `"dead"` status.

---

## Observability, Monitoring & Guardrails

The service exposes OpenTelemetry metrics and traces, integrated with Prometheus and Grafana (`make obs-up`).

### Privacy Guardrails
* **Zero Text in Telemetry:** Candidate application text, LLM prompts, provider response bodies, credentials, and reviewer notes are strictly forbidden from telemetry spans, metric labels, or log outputs.
* **Keyed Actor Hashing:** User subjects are hashed with `TELEMETRY_HASH_SALT` before recording telemetry metrics.

### Prometheus Alert Rules
Pre-configured alerts in `deploy/observability/alerts/screening-alerts.yml` monitor critical operational thresholds:
* `HighOutboxDeadLetterCount`: Triggers when dead-letter outbox events accumulate.
* `AuditChainIntegrityFailure`: Triggers when `app_audit_chain_valid == 0`.
* `HighRateLimitDropRate`: Triggers when rate limiter drops spike.
* `HighAPIErrorRate`: Triggers when HTTP 5xx error rates exceed 5%.

---

## Production Hardening Checklist

Before deploying this service to production environments:

- [ ] **Identity Provider:** Configure a managed OIDC identity provider (e.g., Keycloak, Okta, Auth0) and verify HTTPS transport for issuer/JWKS URLs.
- [ ] **Secret Management:** Inject secrets (`API_KEY`, `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `OPENAI_API_KEY`, `TELEMETRY_HASH_SALT`) via an enterprise secret manager.
- [ ] **Database Least-Privilege:** Grant application database users `SELECT`, `INSERT`, and `UPDATE` permissions on `screenings`, `reviewer_feedback`, `outbox_events`, and `audit_chain_heads`. Restrict `DELETE` and `TRUNCATE` privileges.
- [ ] **TLS & Network Isolation:** Enforce TLS for incoming API traffic and restrict Redis/PostgreSQL access to private VPC networks.
- [ ] **Storage Encryption:** Enable at-rest disk encryption for PostgreSQL databases and backups.
- [ ] **Observability & Alerts:** Deploy the OpenTelemetry collector and configure PagerDuty/Slack notification channels for Prometheus alerts.
- [ ] **AI Ethics & Legal Compliance:** Review automated screening procedures with qualified legal counsel to ensure compliance with local employment and data privacy regulations.
