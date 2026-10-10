# TalentRoute

TalentRoute is a production-oriented Candidate Screening Service that keeps the LangGraph assessment and routing concept but treats output as **decision support only**: it never rejects applicants, schedules interviews, or makes a hiring decision. Every result requires qualified human review.

## Architecture

```text
Client
  | HTTPS + X-API-Key
  v
FastAPI (request validation, auth, CRUD)
  | async SQLAlchemy
  v
PostgreSQL <---- Alembic migrations
  |
  | screening ID enqueued
  v
Redis queue ---> Celery worker ---> LangGraph workflow ---> LangChain/OpenAI-compatible model
                      |                     |
                      +---- updates --------+----> PostgreSQL
```

- **FastAPI / Pydantic v2**: validated REST API, versioned endpoints, uniform status model.
- **PostgreSQL / SQLAlchemy 2 async**: durable screening records, migration-managed schema.
- **Redis / Celery**: asynchronous jobs keep LLM latency off request threads.
- **LangGraph / LangChain**: typed evidence-focused assessments and controlled routing.
- **Docker Compose**: local stack with health checks and one-shot migrations.

## Quick start (Docker)

1. Copy `.env.example` to `.env`.
2. Set `API_KEY` to a long random value and put your `OPENAI_API_KEY` in `.env`. Never commit `.env`.
3. Keep the Compose default PostgreSQL/Redis passwords only for local development. For any shared deployment, supply strong secrets through your secret manager and rotate them.
4. Run:

   ```bash
   docker compose up --build -d
   docker compose logs -f api worker
   ```

5. Open `http://localhost:8000/docs` for the OpenAPI UI. Authenticated endpoints require `X-API-Key` matching `API_KEY`.

Compose injects Redis authentication into service URLs. If running outside Compose, configure `DATABASE_URL`, `REDIS_URL`, `CELERY_BROKER_URL`, and `CELERY_RESULT_BACKEND` in `.env` as appropriate.

## Local development

Requires Python 3.11+ and reachable PostgreSQL/Redis instances.

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# Linux/macOS: source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env  # PowerShell: Copy-Item .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
# In a second terminal:
celery -A app.workers.celery_app:celery_app worker --loglevel=INFO
# Tests that use the injected fake assessor do not call an external LLM:
pytest -q
```

## API

All `/api/v1` endpoints require `X-API-Key` when `API_KEY` is configured. Production mode refuses to start without an API key. TLS should be terminated by a trusted reverse proxy or ingress.

### `POST /api/v1/screenings` (202 Accepted)

```json
{
  "application_text": "Five years building Python APIs with FastAPI, PostgreSQL, and Docker.",
  "job_title": "Python Backend Engineer",
  "required_skills": ["Python", "FastAPI", "PostgreSQL", "Docker"]
}
```

Returns the screening ID and `queued` status. The result is asynchronous; poll the `GET` endpoint.

### `GET /api/v1/screenings/{screening_id}`
Returns status and, once complete, experience and skill evidence, confidence estimates, gaps, and the suggested *human-review queue*. `application_text` is never returned by API read endpoints.

### `GET /api/v1/screenings?limit=50&offset=0`
Paginated listing. Limit is 1-100.

### `DELETE /api/v1/screenings/{screening_id}`
Deletes a record to support retention/privacy workflows. Production deployments should also define a retention schedule, backups policy, legal basis, and access audit trail.

### Health

- `GET /health/live`: process liveness.
- `GET /health/ready`: PostgreSQL and Redis connectivity probe.

## Decision-support and safety design

- Model output is structured and constrained to role-related evidence.
- Application text is treated as untrusted data, not instructions to the model.
- The assessor is instructed not to use protected characteristics or proxies.
- Confidence is a model estimate, not a calibrated probability of job performance.
- `human_review_required` is always true. Suggested routes are `hr_review`, `recruiter_review`, or `manual_review`; there is no auto-reject branch and no external email/calendar side effect.
- Do not use these outputs as the sole basis for employment decisions. Validate for disparate impact, provide accessible human review and appeal paths, and obtain legal/privacy review for your jurisdiction.
- Applicant application text can contain personal data. Restrict database access, use encrypted disks/connections, set retention rules, and avoid sending unnecessary identifiers to an external model.
- Use a dedicated secret manager, TLS, managed database/Redis, least-privilege network access, backups, observability, and resource limits before production deployment. Compose is a deployment baseline, not a substitute for production operations.

## Configuration

See `.env.example`. Important settings include `ENVIRONMENT`, `API_KEY`, `DATABASE_URL`, Redis/Celery URLs, `OPENAI_API_KEY`, `OPENAI_MODEL`, `LLM_BASE_URL`, and `MAX_APPLICATION_CHARS`. `LLM_BASE_URL` supports compatible endpoints, but schema/tool-calling behavior depends on the provider/model.

## Project layout

```text
app/
  api/v1/endpoints/       screening and health endpoints
  core/                   settings, logging, API-key auth
  db/                     async SQLAlchemy base/session/models
  repositories/            database access
  schemas/                request/response and assessment contracts
  services/               orchestration and persistence
  workflows/screening/    LangGraph state, prompts, provider, graph
  workers/                 Celery app and task entry point
  future_features/         documented extension stubs
migrations/                Alembic environment and initial migration
tests/                     graph/schema tests with a fake model provider
```

## Operational notes

- API writes a record before enqueuing. If queue publication fails, the request is marked failed and a 503 is returned; monitor/reconcile such records.
- Celery tasks retry bounded failures. The worker stores a generic error code rather than provider exception text or applicant content.
- Add external metrics/tracing, dead-letter handling, durable audit events, idempotency keys, and a managed secret rotation process before scaling beyond a small deployment.
- See `app/future_features/*.py` for implementation briefs embedded in module docstrings.
