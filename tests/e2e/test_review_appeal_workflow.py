"""E2E workflow tests for reviewer feedback, listing, and appeal outcomes."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_submit_and_list_reviewer_feedback(
    recruiter_client_tenant_a: AsyncClient,
    reviewer_client_tenant_a: AsyncClient,
) -> None:
    # 1. Create screening
    create_res = await recruiter_client_tenant_a.post(
        "/api/v1/screenings",
        json={
            "application_text": "Software engineer with 6 years experience in Python and FastAPI.",
            "job_title": "Software Engineer",
            "required_skills": ["Python", "FastAPI"],
        },
    )
    assert create_res.status_code == 202
    screening_id = create_res.json()["id"]

    # 2. Submit reviewer feedback
    feedback_payload = {
        "label": "agree",
        "reason_codes": ["evidence_missing"],
        "notes": "Strong candidate alignment with stated skills.",
    }
    feedback_res = await reviewer_client_tenant_a.post(
        f"/api/v1/screenings/{screening_id}/feedback",
        json=feedback_payload,
    )
    assert feedback_res.status_code == 201
    feedback_data = feedback_res.json()
    assert feedback_data["record_type"] == "review"
    assert feedback_data["label"] == "agree"
    assert feedback_data["reason_codes"] == ["evidence_missing"]
    assert feedback_data["screening_id"] == screening_id
    feedback_id = feedback_data["id"]

    # 3. List feedback history
    list_res = await reviewer_client_tenant_a.get(
        f"/api/v1/screenings/{screening_id}/feedback"
    )
    assert list_res.status_code == 200
    items = list_res.json()
    assert len(items) == 1
    assert items[0]["id"] == feedback_id


@pytest.mark.asyncio
async def test_record_appeal_outcome_preserves_parent_immutability(
    recruiter_client_tenant_a: AsyncClient,
    reviewer_client_tenant_a: AsyncClient,
    compliance_officer_client_tenant_a: AsyncClient,
) -> None:
    # Create screening
    create_res = await recruiter_client_tenant_a.post(
        "/api/v1/screenings",
        json={
            "application_text": "Backend developer candidate applying for Python engineering role.",
            "job_title": "Python Engineer",
            "required_skills": ["Python"],
        },
    )
    screening_id = create_res.json()["id"]

    # Submit initial review
    review_res = await reviewer_client_tenant_a.post(
        f"/api/v1/screenings/{screening_id}/feedback",
        json={
            "label": "disagree",
            "reason_codes": ["role_requirement_mismatch"],
            "notes": "Initial disagreement on experience match.",
        },
    )
    feedback_id = review_res.json()["id"]

    # Append appeal outcome
    appeal_res = await compliance_officer_client_tenant_a.post(
        f"/api/v1/reviews/{feedback_id}/appeals",
        json={
            "outcome": "revised",
            "notes": "Appeal approved after additional credential review.",
        },
    )
    assert appeal_res.status_code == 201
    appeal_data = appeal_res.json()
    assert appeal_data["record_type"] == "appeal"
    assert appeal_data["parent_feedback_id"] == feedback_id
    assert appeal_data["appeal_outcome"] == "revised"

    # Verify original review remains unchanged in history
    list_res = await reviewer_client_tenant_a.get(
        f"/api/v1/screenings/{screening_id}/feedback"
    )
    assert list_res.status_code == 200
    reviews = list_res.json()
    original = next(item for item in reviews if item["id"] == feedback_id)
    assert original["record_type"] == "review"
    assert original["label"] == "disagree"
    assert original["appeal_outcome"] is None
