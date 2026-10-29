"""Integration tests for app.repositories.screenings module."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.screening import Screening
from app.repositories.screenings import get_screening, list_screenings


@pytest.mark.asyncio
async def test_get_screening_success(db_session: AsyncSession) -> None:
    """Verify get_screening retrieves an existing record with tenant isolation."""
    screening_id = uuid.uuid4()
    tenant_id = "tenant-alpha"

    screening = Screening(
        id=screening_id,
        tenant_id=tenant_id,
        application_text="Experienced Software Engineer applicant.",
        job_title="Senior Python Engineer",
        required_skills=["Python", "FastAPI", "SQLAlchemy"],
        status="queued",
    )
    db_session.add(screening)
    await db_session.commit()

    # Query without tenant filter
    fetched = await get_screening(db_session, screening_id)
    assert fetched is not None
    assert fetched.id == screening_id
    assert fetched.tenant_id == tenant_id
    assert fetched.job_title == "Senior Python Engineer"

    # Query with matching tenant_id
    fetched_tenant = await get_screening(
        db_session, screening_id, tenant_id=tenant_id
    )
    assert fetched_tenant is not None
    assert fetched_tenant.id == screening_id


@pytest.mark.asyncio
async def test_get_screening_for_update(db_session: AsyncSession) -> None:
    """Verify get_screening supports for_update row-level locking option."""
    screening_id = uuid.uuid4()
    tenant_id = "tenant-alpha"

    screening = Screening(
        id=screening_id,
        tenant_id=tenant_id,
        application_text="Lead Architect applicant.",
        job_title="Principal Architect",
        required_skills=["Python", "System Design"],
        status="processing",
    )
    db_session.add(screening)
    await db_session.commit()

    fetched = await get_screening(
        db_session, screening_id, tenant_id=tenant_id, for_update=True
    )
    assert fetched is not None
    assert fetched.id == screening_id


@pytest.mark.asyncio
async def test_get_screening_not_found_and_tenant_mismatch(
    db_session: AsyncSession,
) -> None:
    """Verify get_screening returns None for missing UUID or tenant mismatch."""
    screening_id = uuid.uuid4()
    tenant_id = "tenant-alpha"

    screening = Screening(
        id=screening_id,
        tenant_id=tenant_id,
        application_text="Frontend Developer applicant.",
        job_title="React Engineer",
        required_skills=["TypeScript", "React"],
        status="queued",
    )
    db_session.add(screening)
    await db_session.commit()

    # Mismatching tenant_id
    fetched_other = await get_screening(
        db_session, screening_id, tenant_id="tenant-beta"
    )
    assert fetched_other is None

    # Non-existent UUID
    fetched_missing = await get_screening(db_session, uuid.uuid4())
    assert fetched_missing is None


@pytest.mark.asyncio
async def test_list_screenings_scoping_pagination_ordering(
    db_session: AsyncSession,
) -> None:
    """Verify list_screenings enforces tenant scoping, pagination, and desc ordering."""
    tenant_a = "tenant-alpha"
    tenant_b = "tenant-beta"
    base_time = datetime.now(UTC)

    # Insert 5 records for tenant_a with increasing created_at
    created_ids_a = []
    for i in range(5):
        s_id = uuid.uuid4()
        created_ids_a.append(s_id)
        row = Screening(
            id=s_id,
            tenant_id=tenant_a,
            application_text=f"Applicant {i} text",
            job_title=f"Role {i}",
            required_skills=["Python"],
            status="queued",
            created_at=base_time + timedelta(minutes=i),
        )
        db_session.add(row)

    # Insert 2 records for tenant_b
    for i in range(2):
        row = Screening(
            id=uuid.uuid4(),
            tenant_id=tenant_b,
            application_text=f"Tenant B Applicant {i}",
            job_title="Data Scientist",
            required_skills=["Python", "Pandas"],
            status="queued",
            created_at=base_time + timedelta(minutes=i),
        )
        db_session.add(row)

    await db_session.commit()

    # List all for tenant_a with limit 10, offset 0
    rows_a, count_a = await list_screenings(
        db_session, tenant_id=tenant_a, limit=10, offset=0
    )
    assert count_a == 5
    assert len(rows_a) == 5
    # Verify descending ordering by created_at
    assert rows_a[0].id == created_ids_a[4]
    assert rows_a[4].id == created_ids_a[0]

    # Verify tenant isolation (no tenant_b records in rows_a)
    assert all(row.tenant_id == tenant_a for row in rows_a)

    # Test pagination (limit 2, offset 1)
    page_rows_a, page_count_a = await list_screenings(
        db_session, tenant_id=tenant_a, limit=2, offset=1
    )
    assert page_count_a == 5
    assert len(page_rows_a) == 2
    assert page_rows_a[0].id == created_ids_a[3]
    assert page_rows_a[1].id == created_ids_a[2]

    # Verify count for tenant_b
    rows_b, count_b = await list_screenings(
        db_session, tenant_id=tenant_b, limit=10, offset=0
    )
    assert count_b == 2
    assert len(rows_b) == 2
    assert all(row.tenant_id == tenant_b for row in rows_b)
