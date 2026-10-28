"""Integration tests for Multi-tenant Data Isolation boundaries."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_tenant_cannot_read_other_tenant_screening(
    recruiter_client_tenant_a: AsyncClient,
    recruiter_client_tenant_b: AsyncClient,
) -> None:
    """Verify Tenant B receives 404 when querying Tenant A's screening."""
    create_res = await recruiter_client_tenant_a.post(
        "/api/v1/screenings",
        json={
            "application_text": "Confidential candidate data belonging exclusively to Tenant A.",
            "job_title": "Security Lead",
            "required_skills": ["Python"],
        },
    )
    screening_id = create_res.json()["id"]

    # Tenant B attempt
    res = await recruiter_client_tenant_b.get(f"/api/v1/screenings/{screening_id}")
    assert res.status_code == 404
    assert res.json()["detail"] == "Screening not found"


@pytest.mark.asyncio
async def test_tenant_cannot_list_other_tenant_screenings(
    recruiter_client_tenant_a: AsyncClient,
    recruiter_client_tenant_b: AsyncClient,
) -> None:
    """Verify Tenant B screening list queries do not leak Tenant A records."""
    await recruiter_client_tenant_a.post(
        "/api/v1/screenings",
        json={
            "application_text": "Tenant A candidate submission for list isolation test.",
            "job_title": "Backend Dev",
            "required_skills": ["Python"],
        },
    )

    res_b = await recruiter_client_tenant_b.get("/api/v1/screenings")
    assert res_b.status_code == 200
    data_b = res_b.json()
    assert data_b["count"] == 0
    assert len(data_b["items"]) == 0


@pytest.mark.asyncio
async def test_tenant_cannot_delete_other_tenant_screening(
    recruiter_client_tenant_a: AsyncClient,
    client_factory,
    principal_factory,
) -> None:
    """Verify Tenant B Privacy Officer cannot delete Tenant A screening records."""
    create_res = await recruiter_client_tenant_a.post(
        "/api/v1/screenings",
        json={
            "application_text": "Tenant A screening data deletion boundary protection target.",
            "job_title": "Cloud Engineer",
            "required_skills": ["Python"],
        },
    )
    screening_id = create_res.json()["id"]

    # Tenant B Privacy Officer
    privacy_b = principal_factory(
        subject="privacy-b", tenant_id="tenant_b", roles={"privacy_officer"}
    )
    async with client_factory(privacy_b) as client_b:
        del_res = await client_b.delete(f"/api/v1/screenings/{screening_id}")
        assert del_res.status_code == 404

    # Confirm Tenant A record remains intact
    get_res = await recruiter_client_tenant_a.get(f"/api/v1/screenings/{screening_id}")
    assert get_res.status_code == 200


@pytest.mark.asyncio
async def test_tenant_cannot_submit_review_on_other_tenant_screening(
    recruiter_client_tenant_a: AsyncClient,
    client_factory,
    principal_factory,
) -> None:
    """Verify Tenant B reviewer cannot submit feedback for Tenant A screening."""
    create_res = await recruiter_client_tenant_a.post(
        "/api/v1/screenings",
        json={
            "application_text": "Tenant A screening for cross-tenant review submission test.",
            "job_title": "AI Architect",
            "required_skills": ["Python"],
        },
    )
    screening_id = create_res.json()["id"]

    # Tenant B Reviewer
    reviewer_b = principal_factory(
        subject="reviewer-b", tenant_id="tenant_b", roles={"reviewer"}
    )
    async with client_factory(reviewer_b) as client_b:
        res = await client_b.post(
            f"/api/v1/screenings/{screening_id}/feedback",
            json={"label": "agree", "reason_codes": ["evidence_missing"]},
        )
        assert res.status_code == 404


@pytest.mark.asyncio
async def test_tenant_audit_chain_isolation(
    recruiter_client_tenant_a: AsyncClient,
    recruiter_client_tenant_b: AsyncClient,
    client_factory,
    principal_factory,
) -> None:
    """Verify audit log records and cryptographic chains remain completely isolated across tenants."""
    # Action in Tenant A
    await recruiter_client_tenant_a.post(
        "/api/v1/screenings",
        json={
            "application_text": "Tenant A activity to populate Tenant A audit chain.",
            "job_title": "Developer A",
            "required_skills": ["Python"],
        },
    )

    # Action in Tenant B
    await recruiter_client_tenant_b.post(
        "/api/v1/screenings",
        json={
            "application_text": "Tenant B activity to populate Tenant B audit chain.",
            "job_title": "Developer B",
            "required_skills": ["Python"],
        },
    )

    auditor_a = principal_factory(
        subject="auditor-a", tenant_id="tenant_a", roles={"auditor"}
    )
    auditor_b = principal_factory(
        subject="auditor-b", tenant_id="tenant_b", roles={"auditor"}
    )

    async with client_factory(auditor_a) as client_a:
        res_a = await client_a.get("/api/v1/audit-events")
        items_a = res_a.json()["items"]
        assert all(item["tenant_id"] == "tenant_a" for item in items_a)

    async with client_factory(auditor_b) as client_b:
        res_b = await client_b.get("/api/v1/audit-events")
        items_b = res_b.json()["items"]
        assert all(item["tenant_id"] == "tenant_b" for item in items_b)
