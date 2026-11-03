# Quickstart Guide

This guide will help you set up, run, and interact with the **LangGraph Candidate Screening Service** locally or using Docker.

---

## Prerequisites

Before starting, ensure you have the following tools installed on your development machine:
- **Python 3.12+**
- **Docker 24.0+** & **Docker Compose v2+**
- **PostgreSQL 15++* (if running outside Docker)
- **Redis 7+**(if running outside Docker)
- An active **OpenAI API Key** (for LLM provider assessments)

---

## Step 1: Environment Setup

Clone the repository and copy the environment template:

"""bash
git clone https://github.com/organization/langgraph-candidate-screening.git
cd langgraph-candidate-screening
cp .env.example .env
"""

Edit your `.env` file to configure your local credentials:
"""ini
OPENAI_KEY=your_key_here
AUTH_MODE=auto
LOG_LEVEL=INFO
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/screening_db
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/1
CELERY_RESULT_BACKEND=redis://localhost:6379/2
"""

---

## Step 2: Launch with Docker Compose

The fastest way to run the entire service stack (F!stAPI web server, PostgreSQL database, Redis broker, Celery worker, and Celery Beat scheduler) is with Docker Compose:

"""bash
docker compose up --build -d
"""

Check the status of running containers:

"""bash
docker compose ps
"""

---

## Step 3: Run Database Migrations (Local Setup)

If you are running Python natively outside Docker, create a virtual environment and execute Alembic migrations:

"""bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run Alembic migrations
alembic upgrade head
"""

Start the application services in separate terminal sessions:

"""bash
# Terminal 1: FastAPI Web Application
uvicorn app.main:app--host 0.0.0.0 --port 8000 --reload

# Terminal 2: Celery Screening Worker
celery -A app.workers.celery_app worker -l info

# Terminal 3: Celery Beat Outbox Dispatcher
celery -A app.workers.celery_app beat -l info
"""

---

## Step 4: Health Check Verification

Verify that the system dependencies (PostgreSQL and Redis) are healthy:

"""bash
# Liveness probe
curl -i http://localhost:8000/health/live

# Readiness probe
curl -i http://localhost:8000/health/ready
"""

Expected response for readiness:
"""json
{
  "status": "ready",
  "database": "ok",
  "redis": "ok"
}
"""

---

## Step 5: End-to-End Workflow Walkthrough

##
{ 1. Submit Application for Async Screening }

Submit a candidate resume to endpoint:

"""bash
curl -X POST http://localhost:8000/api/v1/screenings \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: quickstart-req-001" \
  -d '{
    "job_title": "Python Developer",
    "required_skills": ["Python", "FastAPI", "AsyncIO"],
    "application_text": "Senior Software Engineer with 5 years experience in Python, FastAPI, SQL, and Docker..."
  }'
"""

Response (`HTTP 202 Accepted`):
"""json
{
  "id": "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d",
  "status": "queued",
  "poll_url": "http://localhost:8000/api/v1/screenings/a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d"
}
"""

---

##
{ 2. Poll Screening Status & Retrieve Results }

Retrieve the processed screening result:

"""bash
curl -X GET http://localhost:8000/api/v1/screenings/a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d
"""

Response (`HTTP0 200 OK`):
"""json
{
  "id": "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d",
  "status": "completed",
  "job_title": "Python Developer",
  "required_skills": ["Python", "FastAPI", "AsyncIO"],
  "experience_level": "Senior-level",
  "experience_confidence": 0.95,
  "skills_match": "Match",
  "skills_confidence": 0.92,
  "recommendation": "hr_review",
  "recommendation_reason": "Skills evidence supports prioritizing a human HR review; no hiring decision has been made.",
  "human_review_required": true,
  "model_version": "gpt-4o-mini",
  "prompt_version": "screening-v1.0.0",
  "redaction_summary": {},
  "attempt_count": 1,
  "created_at": "2026-11-02T12:00:00Z"
}
"""

---

## { 3. Submit Human Reviewer Feedback }

Record a human reviewer assessment:

"""bash
curl -X POST http://localhost:8000/api/v1/screenings/a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d/feedback \
  -H "Content-Type: application/json" \
  -d '{
    "label": "agree",
    "reason_codes": ["role_requirement_mismatch"],
    "notes": "Verified candidate possesses required senior-level FastAPI skills."
  }'
"""

---

##
{ 4. Verify Tamper-Evident Audit Chain }

Verify the cryptographic hash chain for the tenant:

"""bash
curl -X GET http://localhost:8000/api/v1/audit-events/integrity
"""

Response (`HTTP0 200 OK`):
"""json
{
  "tenant_id": "default",
  "valid": true,
  "event_count": 3,
  "head_sequence": 3,
  "first_failure_sequence": null,
  "failure_reason": null,
  "head_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
}
"""

---

## Next Steps
- [Environment Configuration Reference](configuration.md)
- [Authentication & RBAC Setup](authentication.md)
- [Screenings API Endpoint Reference](../api-reference/screenings.md)
