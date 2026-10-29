"""E2E lifecycle tests: creation, idempotency replay/conflict, polling, and deletion."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_screening_success(recruiter_client_tenant_a: AsyncClient) -> None:
    payload = {
        "application_text": "Experienced Senior Backend Engineer with 8 years in Python, FastAPI, and PostgreSQL.",
        "job_title": "Senior Python Engineer",
        "required_skills": ["Python", "FastAPI", "PostgreSQL"],
    }
    with patch(
        "app.api.v1.endpoints.screenings.enforce_screening_rate_limit",
        new_callable=AsyncMock,
    ):
        response = await recruiter_client_tenant_a.post(
            "/api/v1/screenings", json=payload
        )

    assert response.status_code == 202
    data = response.json()
    assert "id" in data
    assert data["status"] == "queued"
    assert "/api/v1/screenings/" in data["poll_url"]


@pytest.mark.asyncio
async def test_screening_idempotency_replay_and_conflict(
    recruiter_client_tenant_a: AsyncClient,
) -> None:
    idempotency_key = "idempotency-key-e2e-10001"
    payload_a = {
        "application_text": "Candidate with 5 years experience in Python and microservices architecture.",
        "job_title": "Backend Developer",
        "required_skills": ["Python", "Microservices"],
    }
    headers = {"Idempotency-Key": idempotency_key}

    with patch(
        "app.api.v1.endpoints.screenings.enforce_screening_rate_limit",
        new_callable=AsyncMock,
    ):
        res1 = await recruiter_client_tenant_a.post(
            "/api/v1/screenings", json=payload_a, headers=headers
        )
    assert res1.status_code == 202
    data1 = res1.json()

    # Replay identical payload
    with patch(
        "app.api.v1.endpoints.screenings.enforce_screening_rate_limit",
        new_callable=AsyncMock,
    ):
        res2 = await recruiter_client_tenant_a.post(
            "/api/v1/screenings", json=payload_a, headers=headers
        )
    assert res2.status_code == 202
    data2 = res2.json()
    assert data1["id"] == data2["id"]

    # Replay with altered payload
    payload_b = {
        "application_text": "Candidate with 5 years experience in Python and microservices architecture.",
        "job_title": "Altered Job Title",
        "required_skills": ["Python", "Microservices"],
    }
    with patch(
        "app.api.v1.endpoints.screenings.enforce_screening_rate_limit",
        new_callable=AsyncMock,
    ):
        res3 = await recruiter_client_tenant_a.post(
            "/api/v1/screenings", json=payload_b, headers=headers
        )
    assert res3.status_code == 409
    assert "Idempotency-Key has already been used" in res3.json()["detail"]


@pytest.mark.asyncio
async def test_screening_read_list_and_delete_lifecycle(
    recruiter_client_tenant_a: AsyncClient,
    privacy_officer_client_tenant_a: AsyncClient,
) -> None:
    payload = {
        "application_text": "Backend developer candidate specializing in cloud infrastructure and Python.",
        "job_title": "Cloud Engineer",
        "required_skills": ["Python", "AWS", "Docker"],
    }
    with patch(
        "app.api.v1.endpoints.screenings.enforce_screening_rate_limit",
        new_callable=AsyncMock,
    ):
        create_res = await recruiter_client_tenant_a.post(
            "/api/v1/screenings", json=payload
        )
    assert create_res.status_code == 202
    screening_id = create_res.json()["id"]

    # Read screening
    read_res = await recruiter_client_tenant_a.get(f"/api/v1/screenings/{screening_id}")
    assert read_res.status_code == 200
    assert read_res.json()["id"] == screening_id
    assert read_res.json()["job_title"] == "Cloud Engineer"

    # List screenings
    list_res = await recruiter_client_tenant_a.get(
        "/api/v1/screenings?limit=10&offset=0"
    )
    assert list_res.status_code == 200
    list_data = list_res.json()
    assert list_data["count"] >= 1
    assert any(item["id"] == screening_id for item in list_data["items"])

    # Delete screening as Privacy Officer
    delete_res = await privacy_officer_client_tenant_a.delete(
        f"/api/v1/screenings/{screening_id}"
    )
    assert delete_res.status_code == 204

    # Re-read should return 404
    read_res_after = await recruiter_client_tenant_a.get(
        f"/api/v1/screenings/{screening_id}"
    )
    assert read_res_after.status_code == 404
