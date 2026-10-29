"""E2E tests for multi-step audit event creation and cryptographic hash-chain verification."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_audit_logging_and_hash_chain_integrity_flow(
    recruiter_client_tenant_a: AsyncClient,
    reviewer_client_tenant_a: AsyncClient,
    auditor_client_tenant_a: AsyncClient,
) -> None:
    # 1. Action 1: Create screening
    create_res = await recruiter_client_tenant_a.post(
        "/api/v1/screenings",
        json={
            "application_text": "Experienced software engineer applying for audit test role.",
            "job_title": "QA Architect",
            "required_skills": ["Python", "pytest"],
        },
    )
    assert create_res.status_code == 202
    screening_id = create_res.json()["id"]

    # 2. Action 2: Read screening
    read_res = await recruiter_client_tenant_a.get(f"/api/v1/screenings/{screening_id}")
    assert read_res.status_code == 200

    # 3. Action 3: Submit feedback
    feedback_res = await reviewer_client_tenant_a.post(
        f"/api/v1/screenings/{screening_id}/feedback",
        json={
            "label": "agree",
            "reason_codes": ["evidence_missing"],
            "notes": "Verified audit event generation.",
        },
    )
    assert feedback_res.status_code == 201

    # 4. Action 4: Query audit events list
    events_res = await auditor_client_tenant_a.get(
        "/api/v1/audit-events?limit=50&offset=0"
    )
    assert events_res.status_code == 200
    events_data = events_res.json()
    assert events_data["count"] >= 3
    assert len(events_data["items"]) >= 3

    # 5. Action 5: Verify audit hash-chain integrity
    integrity_res = await auditor_client_tenant_a.get("/api/v1/audit-events/integrity")
    assert integrity_res.status_code == 200
    integrity_data = integrity_res.json()

    assert integrity_data["tenant_id"] == "tenant-e2e-a"
    assert integrity_data["valid"] is True
    assert integrity_data["event_count"] >= 4  # Includes audit.integrity_checked itself
    assert integrity_data["first_failure_sequence"] is None
    assert integrity_data["failure_reason"] is None
