# LangGraph Candidate Screening Service

An asynchronous FastAPI service that turns the original LangGraph notebook into a structured, auditable **decision-support** workflow. This implementation adds tenant-scoped OIDC/RBAC, privacy-safe audit events, pre-provider PII minimization, append-only human-review/appeal records, model/prompt provenance, and offline evaluation with blinded pairwise review tools.

**Human review is mandatory.** The service never automatically rejects applicants, makes a hiring decision, schedules an interview, or sends candidate communications. Do not use this output as the sole basis for employment decisions.

## Architecture

```text
Client
  | OIDC bearer token (preferred) or X-API-Key fallback
  v
FastAPI / RBAC / tenant boundary
  |-- PostgreSQL: screenings, hash-chained audit, reviewer feedback, transactional outbox
  |-- Redis: shared rate limit + Celery broker
  |-- Celery Beat -> outbox dispatcher -> Celery worker
                                         |-- conservative PII minimization
                                         |-- LangGraph -> LangChain/provider
                                         |-- prompt/model provenance + processing audit event
```

- **FastAPI / Pydantic v2:** validated REST endpoints.
- **PostgreSQL / SQLAlchemy 2 async / Alembic:** durable screening records and versioned schema migrations.
- **Redis / Celery:** asynchronous assessment work with a PostgreSQL transactional outbox, retry/backoff, lease recovery, and a dedicated Celery Beat process.
- **Submission controls:** tenant/actor-scoped Redis rate limiting, optional `Idempotency-Key` replay protection, request fingerprint conflict detection, and stale-worker recovery.
- **LangGraph / LangChain:** structured evidence-focused assessment; all recommendations go to a human-review queue.
- **OIDC/JWT + RBAC:** signature verification using a cached JWKS client, issuer/audience/expiry validation, role/scope checks, and tenant isolation on screening, review, and audit reads.
- **Audit trail:** per-tenant sequence numbers, SHA-256 hash chaining, allow-listed metadata, integrity verification, and a PostgreSQL mutation guard.
- **PII minimization:** configurable redaction before any provider request; the original application text remains in the restricted database for authorized human review.
- **Quality pipeline:** versioned synthetic starter dataset, schema/agreement/latency/cost and evidence-grounding proxy metrics, confidence intervals, slice reports, conservative report comparison, and blinded A/B review artifacts.

## Quick start with Docker Compose

1. Copy `.env.example` to `.env`.
2. Change `API_KEY`, `POSTGRES_PASSWORD`, and `REDIS_PASSWORD` for a private development stack. Do not use the example credentials in a shared deployment.
3. Add an LLM provider key to `.env`. Do not commit secrets.
4. Run:

   ```bash
   docker compose up --build -d
   docker compose logs -f api worker beat
   ```

5. Browse `http://localhost:8000/docs` in development. The API docs are disabled in production mode.

The Compose file injects service-local PostgreSQL and Redis URLs. A separately deployed stack must supply its own `DATABASE_URL`, `REDIS_URL`, `CELERY_BROKER_URL`, and `CELERY_RESULT_BACKEND` values. Run Alembic migrations before serving requests.

## Local development

Requires Python 3.11+, PostgreSQL, and Redis. For a local process (rather than Compose), set database/broker hostnames to `localhost` in `.env`.

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# Linux/macOS/WSL: source .venv/bin/activate
python -m pip install -e .
cp .env.example .env  # PowerShell: Copy-Item .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
# In a second terminal after activating the same environment:
celery -A app.workers.celery_app:celery_app worker --loglevel=INFO
# In another terminal, run the durable outbox dispatcher schedule:
celery -A app.workers.celery_app:celery_app beat --loglevel=INFO
```

## Authentication and authorization

`AUTH_MODE` supports:

- `oidc`: requires `OIDC_ISSUER`, `OIDC_AUDIENCE`, and `OIDC_JWKS_URL`. JWTs are cryptographically verified; the implementation does not merely decode token payloads. Production requires HTTPS for issuer/JWKS URLs.
- `api_key`: requires `API_KEY` and uses constant-time comparison. It is a simple single-tenant integration mode, not a replacement for a proper identity provider.
- `auto`: accepts verified OIDC bearer tokens when OIDC is configured, otherwise uses the API-key fallback. An invalid provided bearer token is rejected instead of silently falling back to a key.

Configure `OIDC_ROLES_CLAIM`, `OIDC_SCOPES_CLAIM`, `OIDC_TENANT_CLAIM`, and `OIDC_ALGORITHMS` to match the identity provider. The default requires a `tenant_id` claim for OIDC tokens. Algorithms are limited to asymmetric signing algorithms. Do not accept tokens from arbitrary issuers or use a shared HMAC secret supplied by an untrusted client.

| Permission         | Default role membership                               | Purpose                                       |
| ------------------ | ----------------------------------------------------- | --------------------------------------------- |
| `screening:write`  | `recruiter`, `service`                                | Submit work                                   |
| `screening:read`   | `recruiter`, `reviewer`, `auditor`, `privacy_officer` | Read tenant-owned screening results           |
| `screening:delete` | `privacy_officer`                                     | Delete screening/application data             |
| `review:write`     | `reviewer`                                            | Submit a human review record                  |
| `review:read`      | `reviewer`, `auditor`                                 | Read reviewer and appeal history              |
| `review:appeal`    | `compliance_officer`                                  | Append an appeal outcome                      |
| `audit:read`       | `auditor`                                             | Read tenant-scoped audit events               |
| `evaluation:run`   | `model_evaluator`                                     | Reserved for authorized evaluation operations |

The `admin` role has all permissions inside its token's tenant. OIDC scopes may independently grant a permission. Role and claim names should be coordinated with the identity provider. With API-key authentication, the principal is mapped to the `default` tenant; do not use that mode as a multi-tenant identity architecture.

## API overview

All business endpoints require a principal with the appropriate role/scope.

### Submit a screening

`POST /api/v1/screenings` returns `202 Accepted` and a poll URL. Include an optional stable `Idempotency-Key` (8-128 characters from letters, digits, `.`, `_`, `:`, or `-`) when a client may retry after a network timeout. The key is scoped to the authenticated tenant. Repeating the same key with the same normalized payload returns the existing screening; reusing that key with a different payload returns `409 Conflict`. Omitting the header preserves the non-idempotent legacy behavior.

Submission throughput is limited per tenant and authenticated subject by `SCREENING_RATE_LIMIT_REQUESTS` over `SCREENING_RATE_LIMIT_WINDOW_SECONDS`. If Redis is unavailable, submissions fail closed with `503` by default. `RATE_LIMIT_FAIL_OPEN=true` allows an operator to accept requests during a Redis outage, at the cost of bypassing the limit until Redis recovers.

```json
{
  "application_text": "Five years building Python APIs with FastAPI, PostgreSQL, and Docker.",
  "job_title": "Python Backend Engineer",
  "required_skills": ["Python", "FastAPI", "PostgreSQL", "Docker"]
}
```

### Read results

- `GET /api/v1/screenings?limit=50&offset=0` lists tenant-owned records.
- `GET /api/v1/screenings/{screening_id}` returns status and structured evidence, recommendation for a **human-review queue**, model/prompt version, and redaction counts. Raw application text is not returned.
- `DELETE /api/v1/screenings/{screening_id}` deletes the application record. The audit event is retained; associated feedback is detached from the screening through a nullable foreign key.

### Reviewer feedback and appeals

- `POST /api/v1/screenings/{screening_id}/feedback` appends a reviewer label, controlled reason codes, rubric version, and optional notes.
- `GET /api/v1/screenings/{screening_id}/feedback` returns tenant-owned review history.
- `POST /api/v1/reviews/{feedback_id}/appeals` appends an appeal outcome linked to the original feedback. It never overwrites the original reviewer decision or model output.

Human labels include `agree`, `disagree`, `insufficient_evidence`, and `override_recommendation`. Appeal outcomes include `pending`, `upheld`, and `revised`. The database trigger rejects reviewer-feedback updates/deletes, except for the foreign-key detach when the screening itself is deleted; new appeal outcomes are separate linked rows. Reviewer notes can themselves contain sensitive data, so access must be restricted and retention defined. Audit metadata intentionally excludes notes.

### Audit events and health

- `GET /api/v1/audit-events?limit=100&offset=0&action=screening.read` requires audit permission.
- `GET /api/v1/audit-events/integrity` verifies tenant sequence continuity, predecessor links, event hashes, and the stored chain head.
- `GET /health/live` is a liveness check.
- `GET /health/ready` checks database and Redis connectivity.

Audit metadata is allow-listed; the service must never pass full application text, prompts, API keys, provider exception contents, or free-form notes into audit events. The PostgreSQL migration installs a trigger that rejects updates/deletes on `audit_events`. Hash chaining makes row alteration, deletion, or reordering detectable, but a database superuser can disable constraints or alter both events and the chain head. For stronger tamper evidence, periodically export signed head hashes to a separately controlled immutable store. The application role needs `SELECT`/`INSERT` on `audit_events` plus narrowly scoped `SELECT`/`INSERT`/`UPDATE` on `audit_chain_heads`; it should not have audit-table `UPDATE`, `DELETE`, or `TRUNCATE` privileges.

## PII minimization

The worker keeps the original source text in PostgreSQL for authorized reviewers but passes a redacted copy to the LLM. Configuration:

- `PII_REDACTION_ENABLED=true` enables it.
- `PII_REDACTION_POLICY=baseline` masks emails, common phone formats, labelled names/IDs/addresses and similar structured identifiers.
- `PII_REDACTION_POLICY=strict` additionally catches more unlabelled street-address patterns, with higher false-positive risk.

This is a conservative regex baseline, not guaranteed anonymization. It can miss unlabelled, multilingual, misspelled, or novel identifiers and may redact legitimate evidence. For regulated use, replace/augment it with a vetted NER pipeline, localization, human correction, and a documented false-positive/false-negative assessment. Use provider data-processing controls and regional routing as appropriate.

## Offline evaluation and regression gates

The repository includes synthetic, non-applicant fixtures in `datasets/`. Their starter labels are illustrative and must not be represented as a validated hiring benchmark. For production evaluation, use access-controlled, properly licensed/consented data with qualified human-reviewed labels and keep an untouched holdout set.

A prediction JSONL row has `case_id`, `experience_level`, `skills_match`, `recommendation`, `experience_evidence`, and `skills_evidence`. Optional fields include `latency_ms`, `estimated_cost_usd`, `model_version`, `prompt_version`, and `judge_assessment`. See `datasets/schemas/evaluation_prediction.schema.json`.

```bash
# Score a saved candidate output file without calling a provider
python scripts/evaluate_offline.py \
  --dataset datasets/evaluation_v1.jsonl \
  --predictions /secure/path/candidate.predictions.jsonl \
  --model-version provider-model-id \
  --prompt-version screening-prompts-v1.1.0 \
  --output reports/candidate-report.json

# Compare baseline and candidate reports; a failed gate returns exit code 2
python scripts/compare_evaluations.py \
  --baseline /secure/path/baseline-report.json \
  --candidate reports/candidate-report.json \
  --output reports/release-gate.json
```

The evaluation report includes schema validity, exact label agreement against human-gold labels, bootstrap confidence intervals, role-family slices, a lexical evidence-grounding proxy, confidence-calibration diagnostics when confidence scores are provided, prediction distributions, and latency/cost summaries when supplied. Report-to-report release checks compare accuracy, schema validity, slice gaps, output-distribution shift, latency, cost, dataset version, and missing/extra cases. The lexical proxy is not proof of semantic grounding; confidence is explicitly a model estimate and calibration results are unreliable on small datasets. Slice gaps and small sample sizes need human interpretation. An LLM judge may be included in `judge_assessment` to compare judge outputs with human gold; judge agreement is diagnostic and never used as the gold standard. Release gates are conservative operational checks, not proof of fairness, legal compliance, or safe deployment. Passing them does not auto-promote a model or prompt.

### Blinded pairwise review

To produce anonymized A/B response tasks for reviewers:

```bash
python scripts/create_pairwise_review.py \
  --candidate-a /secure/path/model_a.jsonl \
  --candidate-b /secure/path/model_b.jsonl \
  --candidate-a-version model-a-prompt-1 \
  --candidate-b-version model-b-prompt-2 \
  --public-output reports/blinded-review.json \
  --private-map-output .private/blinded-review-map.json
```

Reviewers fill each `judgment.winner` with `a`, `b`, `tie`, or `both_bad`, and may fill a controlled `reason_code`. The private identity map is essential for unblinding and is excluded from version control. Share only the public artifact with reviewers; keep the map in a restricted evaluation workspace. If context is needed, provide a pre-approved redacted `review_context` in the input rows and pass `--include-review-context`. Then summarize judgments using `scripts/score_pairwise_review.py`.

## Database migrations and worker behavior

```bash
alembic upgrade head
```

`0001_initial` creates the original screening table. `0002_enterprise_features` adds tenant/provenance/redaction fields and the audit/feedback tables. `0003_outbox_idempotency` adds optional submission idempotency and the transactional outbox. Run migrations as a controlled deployment step; review downgrade behavior with the database owner before production use.

A screening record, audit event, and `screening.requested` outbox event commit in the same PostgreSQL transaction. The separate Celery Beat process dispatches pending events; leases recover claims after dispatcher crashes and bounded exponential backoff moves repeatedly failing events to `dead`. Inspect `outbox_events` operationally for `dead` rows and reconcile the cause before requeueing. To retry one event after fixing the cause, run `python scripts/requeue_dead_outbox.py --event-id <event-uuid>`; use `--all-dead --limit 100` only for an intentional batch retry. Broker delivery can be at-least-once, so consumers must remain idempotent. A duplicate worker task is ignored while the record is already running; a running record older than `STALE_PROCESSING_SECONDS` can be reclaimed after a worker crash.

Error details stored on screening records are stable codes only. The task writes processing success/failure audit events without saving provider exception text. Redis and the broker should be reachable only on private networks and configured with strong credentials.

## Observability and Monitoring

The service includes vendor-neutral OpenTelemetry instrumentation (metrics and traces) and structured JSON logging adhering to strict privacy guardrails (no applicant text or prompts in telemetry). Use `make obs-up` to launch local observability stack (Collector, Prometheus, Grafana).

### Alerting and Runbooks

Prometheus alerts monitor dead-letter outboxes, audit chain validity, and 5xx error rates. Refer to `docs/observability.md` for complete operator runbooks and operational recovery commands.

## Production hardening checklist

Before production use, configure a managed OIDC provider and role mapping, TLS/mTLS where appropriate, secret-manager injection and rotation, non-default PostgreSQL/Redis credentials, database least-privilege grants, encrypted disks/backups, retention and deletion schedules, external logs/metrics/traces, queue depth and failure alerts, rate limits, request-size limits, disaster recovery, provider privacy terms, and jurisdiction-specific employment/AI/privacy legal review. Include human appeal and accessibility paths and audit disparate impact before using any model output in a consequential process.
