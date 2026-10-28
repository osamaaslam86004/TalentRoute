"""Integration tests for Candidate Screening endpoints."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.outbox_event import OutboxEvent
from app.db.models.screening import Screening


@pytest.mark.asyncio
async def test_create_screening_success(
    recruiter_client_tenant_a: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Test successful submission of a candidate screening request."""
    payload = {
        "application_text": "Experienced Senior Python Developer with 8 years in FastAPI.",
        "job_title": "Principal Backend Engineer",
        "required_skills": ["Python", "FastAPI", "SQLAlchemy"],
    }
    response = await recruiter_client_tenant_a.post(
        "/api/v1/screenings", json=payload
    )
    assert response.status_code == 202
    data = response.json()
    assert "id" in data
    assert data["status"] == "queued"
    assert "poll_url" in data

    screening_id = uuid.UUID(data["id"])
    screening = await db_session.get(Screening, screening_id)
    assert screening is not None
    assert screening.tenant_id == "tenant_a"
    assert screening.status == "queued"

    # Verify outbox event creation for async Celery pipeline dispatch
    outbox = await db_session.scalar(
        select(OutboxEvent).where(OutboxEvent.aggregate_id == str(screening_id))
    )
    assert outbox is not None
    assert outbox.event_type == "screening.requested"


@pytest.mark.asyncio
async def test_create_screening_payload_size_limit(
    recruiter_client_tenant_a: AsyncClient,
) -> None:
    """Verify HTTP 413 error when application text exceeds configured limits."""
    payload = {
        "application_text": "A" * 150000,
        "job_title": "Python Developer",
        "required_skills": ["Python"],
    }
    response = await recruiter_client_tenant_a.post(
        "/api/v1/screenings", json=payload
    )
    assert response.status_code == 413
    assert "exceeds configured limit" in response.json()["detail"]


@pytest.mark.asyncio
async def test_screening_idempotency_replay_identical_payload(
    recruiter_client_tenant_a: AsyncClient,
) -> None:
    """Verify replaying an identical request returns cached response with HTTP 202."""
    headers = {"Idempotency-Key": "idempotent-key-001"}
    payload = {
        "application_text": "Candidate with extensive microservices background.",
        "job_title": "Staff Engineer",
        "required_skills": ["Python", "Docker"],
    }

    # Initial request
    res1 = await recruiter_client_tenant_a.post(
        "/api/v1/screenings", json=payload, headers=headers
    )
    assert res1.status_code == 202
    data1 = res1.json()

    # Replayed identical request
    res2 = await recruiter_client_tenant_a.post(
        "/api/v1/screenings", json=payload, headers=headers
    )
    assert res2.status_code == 202
    data2 = res2.json()

    assert data1["id"] == data2["id"]
    assert data1["poll_url"] == data2["poll_url"]


@pytest.mark.asyncio
async def test_screening_idempotency_conflict_modified_payload(
    recruiter_client_tenant_a: AsyncClient,
) -> None:
    """Verify reusing an idempotency key with a altered payload returns HTTP 409 Conflict."""
    headers = {"Idempotency-Key": "idempotent-key-002"}
    payload_orig = {
        "application_text": "Original application details for candidate.",
        "job_title": "Software Engineer",
        "required_skills": ["Python"],
    }
    payload_mod = {
        "application_text": "Modified application details for candidate.",
        "job_title": "Senior Software Engineer",
        "required_skills": ["Python"],
    }

    res1 = await recruiter_client_tenant_a.post(
        "/api/v1/screenings", json=payload_orig, headers=headers
    )
    assert res1.status_code == 202

    res2 = await recruiter_client_tenant_a.post(
        "/api/v1/screenings", json=payload_mod, headers=headers
    )
    assert res2.status_code == 409
    assert "used with a different request" in res2.json()["detail"]


@pytest.mark.asyncio
async def test_get_screenings_list_pagination(
    recruiter_client_tenant_a: AsyncClient,
) -> None:
    """Test paginated retrieval of candidate screening collection."""
    for i in range(3):
        await recruiter_client_tenant_a.post(
            "/api/v1/screenings",
            json={
                "application_text": f"Candidate application number {i + 1} with skill details.",
                "job_title": "Developer",
                "required_skills": ["Python"],
            },
        )

    response = await recruiter_client_tenant_a.get(
        "/api/v1/screenings?limit=2&offset=0"
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 2
    assert data["count"] == 3
    assert data["limit"] == 2
    assert data["offset"] == 0


@pytest.mark.asyncio
async def test_get_screening_detail_success(
    recruiter_client_tenant_a: AsyncClient,
) -> None:
    """Test retrieving screening details by ID."""
    create_res = await recruiter_client_tenant_a.post(
        "/api/v1/screenings",
        json={
            "application_text": "Application text for detailed retrieval test.",
            "job_title": "Data Engineer",
            "required_skills": ["Python", "SQL"],
        },
    )
    screening_id = create_res.json()["id"]

    response = await recruiter_client_tenant_a.get(
        f"/api/v1/screenings/{screening_id}"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == screening_id
    assert data["job_title"] == "Data Engineer"


@pytest.mark.asyncio
async def test_get_screening_detail_not_found(
    recruiter_client_tenant_a: AsyncClient,
) -> None:
    """Verify HTTP 404 for nonexistent screening ID."""
    random_id = uuid.uuid4()
    response = await recruiter_client_tenant_a.get(
        f"/api/v1/screenings/{random_id}"
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_screening_success(
    privacy_officer_client_tenant_a: AsyncClient,
    recruiter_client_tenant_a: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Test deletion of a candidate screening by an authorized Privacy Officer."""
    create_res = await recruiter_client_tenant_a.post(
        "/api/v1/screenings",
        json={
            "application_text": "Candidate data subject request deletion target.",
            "job_title": "Security Analyst",
            "required_skills": ["Python"],
        },
    )
    screening_id = create_res.json()["id"]

    del_res = await privacy_officer_client_tenant_a.delete(
        f"/api/v1/screenings/{screening_id}"
    )
    assert del_res.status_code == 204

    deleted_row = await db_session.get(Screening, uuid.UUID(screening_id))
    assert deleted_row is None
