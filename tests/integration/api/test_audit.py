"""Integration tests for Audit Chain Integrity and Verification endpoints."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_audit_chain_integrity_valid(
    recruiter_client_tenant_a: AsyncClient,
    reviewer_client_tenant_a: AsyncClient,
    auditor_client_tenant_a: AsyncClient,
) -> None:
    """Verify cryptographically chained audit events pass hash verification."""
    # Execute a sequence of auditable operations
    create_res = await recruiter_client_tenant_a.post(
        "/api/v1/screenings",
        json={
            "application_text": "Audit trail generation application text sample.",
            "job_title": "Backend Architect",
            "required_skills": ["Python"],
        },
    )
    screening_id = create_res.json()["id"]

    await reviewer_client_tenant_a.post(
        f"/api/v1/screenings/{screening_id}/feedback",
        json={"label": "agree", "reason_codes": ["evidence_missing"]},
    )

    await recruiter_client_tenant_a.get(f"/api/v1/screenings/{screening_id}")

    # Verify audit integrity
    res = await auditor_client_tenant_a.get("/api/v1/audit-events/integrity")
    assert res.status_code == 200
    data = res.json()
    assert data["valid"] is True
    assert data["event_count"] >= 3
    assert data["first_failure"] is None


@pytest.mark.asyncio
async def test_get_audit_events_list_and_filtering(
    recruiter_client_tenant_a: AsyncClient,
    auditor_client_tenant_a: AsyncClient,
) -> None:
    """Test querying audit events with action-based filtering."""
    await recruiter_client_tenant_a.post(
        "/api/v1/screenings",
        json={
            "application_text": "Filtering test candidate application text.",
            "job_title": "QA Engineer",
            "required_skills": ["Python", "pytest"],
        },
    )

    # Filter by specific action
    res = await auditor_client_tenant_a.get(
        "/api/v1/audit-events?action=screening.created"
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data["items"]) >= 1
    assert all(item["action"] == "screening.created" for item in data["items"])


@pytest.mark.asyncio
async def test_audit_events_unauthorized_forbidden(
    recruiter_client_tenant_a: AsyncClient,
) -> None:
    """Verify non-auditor roles are forbidden from querying audit logs."""
    res = await recruiter_client_tenant_a.get("/api/v1/audit-events")
    assert res.status_code == 403
    assert "Insufficient permissions" in res.json()["detail"]
