"""Integration tests for Human Reviewer Feedback and Appeal endpoints."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_submit_feedback_success(
    recruiter_client_tenant_a: AsyncClient,
    reviewer_client_tenant_a: AsyncClient,
) -> None:
    """Test submitting human reviewer feedback on a completed screening."""
    create_res = await recruiter_client_tenant_a.post(
        "/api/v1/screenings",
        json={
            "application_text": "Application for reviewer feedback assessment test.",
            "job_title": "Lead Architect",
            "required_skills": ["Python", "AWS"],
        },
    )
    screening_id = create_res.json()["id"]

    feedback_payload = {
        "label": "agree",
        "reason_codes": ["evidence_missing"],
        "notes": "Verified candidate qualifications and concurred with model recommendation.",
    }

    res = await reviewer_client_tenant_a.post(
        f"/api/v1/screenings/{screening_id}/feedback",
        json=feedback_payload,
    )
    assert res.status_code == 201
    data = res.json()
    assert data["screening_id"] == screening_id
    assert data["label"] == "agree"
    assert data["reason_codes"] == ["evidence_missing"]
    assert data["record_type"] == "review"


@pytest.mark.asyncio
async def test_submit_feedback_invalid_reason_codes(
    recruiter_client_tenant_a: AsyncClient,
    reviewer_client_tenant_a: AsyncClient,
) -> None:
    """Verify HTTP 422 validation error when submitting unapproved reason codes."""
    create_res = await recruiter_client_tenant_a.post(
        "/api/v1/screenings",
        json={
            "application_text": "Application for reason code validation test.",
            "job_title": "DevOps Engineer",
            "required_skills": ["Python"],
        },
    )
    screening_id = create_res.json()["id"]

    feedback_payload = {
        "label": "disagree",
        "reason_codes": ["invalid_custom_reason_code"],
        "notes": "Invalid reason code test.",
    }

    res = await reviewer_client_tenant_a.post(
        f"/api/v1/screenings/{screening_id}/feedback",
        json=feedback_payload,
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_submit_feedback_screening_not_found(
    reviewer_client_tenant_a: AsyncClient,
) -> None:
    """Verify HTTP 404 when submitting feedback for non-existent screening."""
    random_id = uuid.uuid4()
    feedback_payload = {
        "label": "agree",
        "reason_codes": ["evidence_missing"],
    }
    res = await reviewer_client_tenant_a.post(
        f"/api/v1/screenings/{random_id}/feedback",
        json=feedback_payload,
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_list_feedback_history(
    recruiter_client_tenant_a: AsyncClient,
    reviewer_client_tenant_a: AsyncClient,
) -> None:
    """Test listing feedback history for a given candidate screening."""
    create_res = await recruiter_client_tenant_a.post(
        "/api/v1/screenings",
        json={
            "application_text": "Application for listing review feedback history.",
            "job_title": "Fullstack Developer",
            "required_skills": ["Python", "React"],
        },
    )
    screening_id = create_res.json()["id"]

    # Submit initial feedback
    await reviewer_client_tenant_a.post(
        f"/api/v1/screenings/{screening_id}/feedback",
        json={"label": "agree", "reason_codes": ["evidence_missing"]},
    )

    # Submit second feedback entry
    await reviewer_client_tenant_a.post(
        f"/api/v1/screenings/{screening_id}/feedback",
        json={"label": "override_recommendation", "reason_codes": ["role_requirement_mismatch"]},
    )

    res = await reviewer_client_tenant_a.get(
        f"/api/v1/screenings/{screening_id}/feedback"
    )
    assert res.status_code == 200
    items = res.json()
    assert len(items) == 2


@pytest.mark.asyncio
async def test_record_appeal_outcome_success(
    recruiter_client_tenant_a: AsyncClient,
    reviewer_client_tenant_a: AsyncClient,
    compliance_officer_client_tenant_a: AsyncClient,
) -> None:
    """Test appending an appeal decision to an existing feedback record."""
    create_res = await recruiter_client_tenant_a.post(
        "/api/v1/screenings",
        json={
            "application_text": "Candidate application subject to formal appeal.",
            "job_title": "Site Reliability Engineer",
            "required_skills": ["Python", "Kubernetes"],
        },
    )
    screening_id = create_res.json()["id"]

    feedback_res = await reviewer_client_tenant_a.post(
        f"/api/v1/screenings/{screening_id}/feedback",
        json={"label": "disagree", "reason_codes": ["rubric_ambiguity"]},
    )
    feedback_id = feedback_res.json()["id"]

    appeal_payload = {
        "outcome": "upheld",
        "notes": "Compliance officer upheld original reviewer disagreement.",
    }

    appeal_res = await compliance_officer_client_tenant_a.post(
        f"/api/v1/reviews/{feedback_id}/appeals",
        json=appeal_payload,
    )
    assert appeal_res.status_code == 201
    data = appeal_res.json()
    assert data["parent_feedback_id"] == feedback_id
    assert data["appeal_outcome"] == "upheld"
    assert data["record_type"] == "appeal"
