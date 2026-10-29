"""Integration tests for screening schemas, ORM round-trips, and endpoint payloads."""

import uuid
import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.screening import Screening
from app.schemas.assessment import ExperienceLevel, Recommendation, SkillMatch
from app.schemas.screening import (
    ScreeningAccepted,
    ScreeningCreateRequest,
    ScreeningList,
    ScreeningRead,
)


def test_screening_create_request_normalization():
    payload = ScreeningCreateRequest(
        application_text="   This is a valid candidate application text with over 20 chars.   ",
        job_title="  Senior Backend Engineer  ",
        required_skills=[" Python ", "Python", " FastAPI ", "SQLAlchemy "],
    )
    assert (
        payload.application_text
        == "This is a valid candidate application text with over 20 chars."
    )
    assert payload.job_title == "Senior Backend Engineer"
    assert payload.required_skills == ["Python", "FastAPI", "SQLAlchemy"]


def test_screening_create_request_blank_application():
    with pytest.raises(ValidationError):
        ScreeningCreateRequest(
            application_text="   ",
            job_title="Developer",
            required_skills=["Python"],
        )


def test_screening_create_request_short_application():
    with pytest.raises(ValidationError):
        ScreeningCreateRequest(
            application_text="Too short",
            job_title="Developer",
            required_skills=["Python"],
        )


def test_screening_create_request_empty_skills():
    with pytest.raises(ValidationError):
        ScreeningCreateRequest(
            application_text="Valid application text exceeding twenty characters.",
            job_title="Developer",
            required_skills=["  ", ""],
        )


def test_screening_create_request_forbidden_extra():
    with pytest.raises(ValidationError):
        ScreeningCreateRequest(
            application_text="Valid application text exceeding twenty characters.",
            job_title="Developer",
            required_skills=["Python"],
            unexpected_field="Not allowed",
        )


@pytest.mark.asyncio
async def test_screening_read_orm_round_trip(db_session: AsyncSession):
    screening_id = uuid.uuid4()
    orm_screening = Screening(
        id=screening_id,
        tenant_id="tenant-schema-test",
        request_id="req-1001",
        idempotency_key="idempotency-key-1001",
        status="completed",
        application_text="Candidate with extensive backend expertise in Python and FastAPI.",
        job_title="Senior Python Engineer",
        required_skills=["Python", "FastAPI"],
        experience_level="Senior-level",
        experience_confidence=0.92,
        experience_evidence=["10 years backend experience"],
        skills_match="Match",
        skills_confidence=0.89,
        skills_evidence=["Built distributed microservices"],
        skill_gaps=[],
        recommendation="recruiter_review",
        recommendation_reason="Strong fit for core requirements.",
        human_review_required=True,
        model_version="gpt-4o-mini-2024-07-18",
        prompt_version="v2.1",
        redaction_summary={"email": 1, "phone": 1},
        attempt_count=1,
    )
    db_session.add(orm_screening)
    await db_session.commit()
    await db_session.refresh(orm_screening)

    schema_read = ScreeningRead.model_validate(orm_screening)
    assert schema_read.id == screening_id
    assert schema_read.status == "completed"
    assert schema_read.job_title == "Senior Python Engineer"
    assert schema_read.experience_level == ExperienceLevel.SENIOR_LEVEL
    assert schema_read.skills_match == SkillMatch.MATCH
    assert schema_read.recommendation == Recommendation.RECRUITER_REVIEW
    assert schema_read.experience_confidence == 0.92
    assert schema_read.redaction_summary == {"email": 1, "phone": 1}


@pytest.mark.asyncio
async def test_post_screening_endpoint_validation_success(async_client: AsyncClient):
    response = await async_client.post(
        "/api/v1/screenings",
        headers={"Idempotency-Key": "key-test-12345678"},
        json={
            "application_text": "Experienced Python Developer with expertise in FastAPI and SQLAlchemy async.",
            "job_title": "Lead Python Engineer",
            "required_skills": ["Python", "FastAPI"],
        },
    )
    assert response.status_code == 202
    data = response.json()
    accepted = ScreeningAccepted(**data)
    assert accepted.status == "queued"
    assert "poll_url" in data


@pytest.mark.asyncio
async def test_post_screening_endpoint_validation_failure(async_client: AsyncClient):
    # Short application_text < 20 chars
    response = await async_client.post(
        "/api/v1/screenings",
        json={
            "application_text": "Short text",
            "job_title": "Developer",
            "required_skills": ["Python"],
        },
    )
    assert response.status_code == 422

    # Forbidden extra field
    response = await async_client.post(
        "/api/v1/screenings",
        json={
            "application_text": "Valid application text exceeding twenty characters.",
            "job_title": "Developer",
            "required_skills": ["Python"],
            "extra_field": "disallowed",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_screenings_endpoint_list_response(
    async_client: AsyncClient,
    db_session: AsyncSession,
):
    orm_screening = Screening(
        id=uuid.uuid4(),
        tenant_id="tenant-schema-test",
        status="queued",
        application_text="Valid application text exceeding twenty characters for list test.",
        job_title="Backend Developer",
        required_skills=["Python"],
        human_review_required=True,
    )
    db_session.add(orm_screening)
    await db_session.commit()

    response = await async_client.get("/api/v1/screenings")
    assert response.status_code == 200
    data = response.json()
    screening_list = ScreeningList(**data)
    assert screening_list.count >= 1
    assert len(screening_list.items) >= 1
    assert screening_list.items[0].job_title == "Backend Developer"
