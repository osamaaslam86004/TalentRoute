"""E2E security and rate-limiting tests: 401 unauth, 403 forbidden, and 429 rate limits."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException, status
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_unauthenticated_requests_return_401(
    unauthenticated_client: AsyncClient,
) -> None:
    res1 = await unauthenticated_client.get("/api/v1/screenings")
    assert res1.status_code == 401

    res2 = await unauthenticated_client.get("/api/v1/audit-events/integrity")
    assert res2.status_code == 401


@pytest.mark.asyncio
async def test_insufficient_permissions_return_403_forbidden(
    recruiter_client_tenant_a: AsyncClient,
    reviewer_client_tenant_a: AsyncClient,
) -> None:
    # 1. Recruiter attempting to access audit integrity (requires audit:read)
    res1 = await recruiter_client_tenant_a.get("/api/v1/audit-events/integrity")
    assert res1.status_code == 403
    assert "Insufficient permissions" in res1.json()["detail"]

    # 2. Reviewer attempting to delete a screening (requires screening:delete)
    import uuid

    dummy_id = str(uuid.uuid4())
    res2 = await reviewer_client_tenant_a.delete(f"/api/v1/screenings/{dummy_id}")
    assert res2.status_code == 403
    assert "Insufficient permissions" in res2.json()["detail"]


@pytest.mark.asyncio
async def test_rate_limiting_enforcement_returns_429(
    recruiter_client_tenant_a: AsyncClient,
) -> None:
    payload = {
        "application_text": "Candidate application text for testing rate limiting thresholds.",
        "job_title": "Software Architect",
        "required_skills": ["Python"],
    }

    async def _mock_rate_limit_exceeded(request, principal):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Screening submission rate limit exceeded",
            headers={"Retry-After": "60"},
        )

    with patch(
        "app.api.v1.endpoints.screenings.enforce_screening_rate_limit",
        side_effect=_mock_rate_limit_exceeded,
    ):
        res = await recruiter_client_tenant_a.post("/api/v1/screenings", json=payload)

    assert res.status_code == 429
    assert res.json()["detail"] == "Screening submission rate limit exceeded"
    assert res.headers["Retry-After"] == "60"
