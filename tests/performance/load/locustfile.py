"""Distributed load testing user journeys using Locust for Candidate Screening API."""

from __future__ import annotations

import random
import uuid

from locust import HttpUser, between, task


class CandidateScreeningUser(HttpUser):
    """Simulates realistic end-to-end user load across screening, review, and audit endpoints."""

    wait_time = between(0.2, 1.5)

    def on_start(self) -> None:
        """Initialize user authentication headers and shared state."""
        self.tenant_id = f"load-tenant-{random.randint(1, 10):02d}"
        self.headers = {
            "Content-Type": "application/json",
            "X-API-Key": "dev-key",
            "X-Tenant-ID": self.tenant_id,
        }
        self.created_screening_ids: list[str] = []

    @task(4)
    def submit_screening(self) -> None:
        """Task 1: Submit candidate screening request with optional idempotency keys."""
        idempotency_key = (
            f"locust-key-{uuid.uuid4()}" if random.random() > 0.3 else None
        )
        headers = dict(self.headers)
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        payload = {
            "application_text": (
                "Senior Python Developer with 8 years experience building FastAPI, "
                "SQLAlchemy, PostgreSQL microservices and Celery task pipelines. "
                "Contact: candidate@example.com, Phone: +1-555-0199."
            ),
            "job_title": "Senior Python Backend Engineer",
            "required_skills": ["Python", "FastAPI", "SQLAlchemy", "PostgreSQL"],
        }

        with self.client.post(
            "/api/v1/screenings",
            json=payload,
            headers=headers,
            catch_response=True,
            name="POST /api/v1/screenings",
        ) as response:
            if response.status_code in (202, 200):
                try:
                    data = response.json()
                    if "id" in data:
                        self.created_screening_ids.append(data["id"])
                    response.success()
                except Exception as exc:
                    response.failure(f"Failed to parse JSON response: {exc}")
            else:
                response.failure(f"Unexpected status code: {response.status_code}")

    @task(3)
    def poll_screening_status(self) -> None:
        """Task 2: Poll screening status and evaluation details."""
        if not self.created_screening_ids:
            return

        screening_id = random.choice(self.created_screening_ids)
        with self.client.get(
            f"/api/v1/screenings/{screening_id}",
            headers=self.headers,
            catch_response=True,
            name="GET /api/v1/screenings/{id}",
        ) as response:
            if response.status_code in (200, 404):
                response.success()
            else:
                response.failure(f"Polling failed with status: {response.status_code}")

    @task(2)
    def submit_reviewer_feedback(self) -> None:
        """Task 3: Submit human reviewer feedback for an existing screening."""
        if not self.created_screening_ids:
            return

        screening_id = random.choice(self.created_screening_ids)
        payload = {
            "label": "agree",
            "reason_codes": ["evidence_missing"],
            "notes": "Verified evidence matches required backend skill definitions.",
        }

        with self.client.post(
            f"/api/v1/screenings/{screening_id}/feedback",
            json=payload,
            headers=self.headers,
            catch_response=True,
            name="POST /api/v1/screenings/{id}/feedback",
        ) as response:
            if response.status_code in (201, 404):
                response.success()
            else:
                response.failure(
                    f"Feedback submission failed with status: {response.status_code}"
                )

    @task(1)
    def check_audit_integrity(self) -> None:
        """Task 4: Check tenant audit hash-chain integrity."""
        with self.client.get(
            "/api/v1/audit-events/integrity",
            headers=self.headers,
            catch_response=True,
            name="GET /api/v1/audit-events/integrity",
        ) as response:
            if response.status_code == 200:
                data = response.json()
                if data.get("valid") is True:
                    response.success()
                else:
                    response.failure(f"Audit chain invalid for tenant {self.tenant_id}")
            else:
                response.failure(
                    f"Audit integrity check failed with status: {response.status_code}"
                )
