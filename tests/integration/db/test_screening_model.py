"""Integration tests for the Screening database model."""

import uuid
from datetime import UTC, datetime
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models.screening import Screening


@pytest.mark.asyncio
async def test_screening_persistence_and_retrieval(db_session: AsyncSession) -> None:
    """Test full persistence and async retrieval of a Screening record."""
    screening_id = uuid.uuid4()
    now = datetime.now(UTC)
    screening = Screening(
        id=screening_id,
        tenant_id="tenant-alpha",
        request_id="req-12345",
        status="queued",
        application_text="Senior Python developer with 8 years of FastAPI experience.",
        job_title="Lead Backend Engineer",
        required_skills=["Python", "FastAPI", "SQLAlchemy"],
        experience_level="Senior",
        experience_confidence=0.95,
        experience_evidence=["8 years developer experience"],
        skills_match="High",
        skills_confidence=0.90,
        skills_evidence=["Proficient in FastAPI"],
        skill_gaps=["Kubernetes"],
        recommendation="Proceed",
        recommendation_reason="Strong backend candidate.",
        human_review_required=True,
        model_version="gpt-4o-v1",
        prompt_version="screening-v2",
        redaction_summary={"pii_names_redacted": 2, "pii_emails_redacted": 1},
        attempt_count=1,
        created_at=now,
        updated_at=now,
    )

    db_session.add(screening)
    await db_session.flush()

    stmt = select(Screening).where(Screening.id == screening_id)
    retrieved = await db_session.scalar(stmt)

    assert retrieved is not None
    assert retrieved.id == screening_id
    assert retrieved.tenant_id == "tenant-alpha"
    assert retrieved.job_title == "Lead Backend Engineer"
    assert retrieved.required_skills == ["Python", "FastAPI", "SQLAlchemy"]
    assert retrieved.redaction_summary == {
        "pii_names_redacted": 2,
        "pii_emails_redacted": 1,
    }
    assert retrieved.human_review_required is True
    assert retrieved.status == "queued"


@pytest.mark.asyncio
async def test_screening_idempotency_unique_constraint(
    db_session: AsyncSession,
) -> None:
    """Verify conditional unique idempotency constraint on non-null keys."""
    tenant = "tenant-beta"
    key = "idempotency-key-001"

    s1 = Screening(
        tenant_id=tenant,
        idempotency_key=key,
        application_text="Application 1 text body for screening.",
        job_title="Software Engineer",
        required_skills=["Python"],
    )
    db_session.add(s1)
    await db_session.flush()

    s2 = Screening(
        tenant_id=tenant,
        idempotency_key=key,
        application_text="Application 2 text body for screening.",
        job_title="Software Engineer",
        required_skills=["Python"],
    )
    db_session.add(s2)

    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_screening_null_idempotency_keys_allowed(
    db_session: AsyncSession,
) -> None:
    """Verify multiple records with NULL idempotency_key are permitted."""
    tenant = "tenant-gamma"

    s1 = Screening(
        tenant_id=tenant,
        idempotency_key=None,
        application_text="App 1 without key.",
        job_title="Data Scientist",
        required_skills=["Python"],
    )
    s2 = Screening(
        tenant_id=tenant,
        idempotency_key=None,
        application_text="App 2 without key.",
        job_title="Data Scientist",
        required_skills=["Python"],
    )

    db_session.add_all([s1, s2])
    await db_session.flush()

    stmt = select(Screening).where(Screening.tenant_id == tenant)
    rows = (await db_session.scalars(stmt)).all()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_screening_index_queries(db_session: AsyncSession) -> None:
    """Verify status and tenant/created_at index query capabilities."""
    tenant = "tenant-delta"
    s1 = Screening(
        tenant_id=tenant,
        status="completed",
        application_text="Candidate 1 text",
        job_title="DevOps Engineer",
        required_skills=["Docker"],
    )
    s2 = Screening(
        tenant_id=tenant,
        status="processing",
        application_text="Candidate 2 text",
        job_title="DevOps Engineer",
        required_skills=["Terraform"],
    )
    db_session.add_all([s1, s2])
    await db_session.flush()

    stmt = (
        select(Screening)
        .where(Screening.tenant_id == tenant, Screening.status == "completed")
        .order_by(Screening.created_at.desc())
    )
    results = (await db_session.scalars(stmt)).all()
    assert len(results) == 1
    assert results[0].status == "completed"

    index_names = {idx.name for idx in Screening.__table__.indexes}
    assert "ix_screenings_status_created_at" in index_names
    assert "ix_screenings_tenant_created_at" in index_names
    assert "uq_screenings_tenant_idempotency" in index_names
