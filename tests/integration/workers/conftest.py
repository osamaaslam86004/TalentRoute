"""Shared async test fixtures for worker and outbox infrastructure integration tests."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models.outbox_event import OutboxEvent
from app.db.models.screening import Screening


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db_engine():
    """Create an in-memory SQLite engine with StaticPool for async worker testing."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(db_engine):
    """Return an async_sessionmaker bound to the test database engine."""
    return async_sessionmaker(
        bind=db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


@pytest_asyncio.fixture
async def db_session(session_factory) -> AsyncGenerator[AsyncSession, None]:
    """Yield a transactional AsyncSession, rolling back changes after test execution."""
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def patch_session_local(monkeypatch, session_factory):
    """Monkeypatch AsyncSessionLocal across worker modules to use test database session."""
    monkeypatch.setattr("app.workers.outbox.AsyncSessionLocal", session_factory)
    monkeypatch.setattr("app.workers.tasks.process_screening", MagicMock())
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", session_factory)
    (
        monkeypatch.setattr(
            "app.workers.pipeline_stats.AsyncSessionLocal", session_factory
        ),
    )
    (monkeypatch.setattr("app.db.session.AsyncSessionLocal", session_factory),)

    return session_factory


@pytest.fixture
def make_screening():
    """Factory fixture to create populated Screening model instances."""

    def _factory(
        *,
        tenant_id: str = "tenant_a",
        job_title: str = "Senior Python Engineer",
        application_text: str = (
            "Experienced backend engineer with 8 years of Python, FastAPI, "
            "and PostgreSQL experience."
        ),
        required_skills: list[str] | None = None,
        status: str = "queued",
        idempotency_key: str | None = None,
    ) -> Screening:
        return Screening(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            job_title=job_title,
            application_text=application_text,
            required_skills=required_skills or ["Python", "FastAPI", "SQLAlchemy"],
            status=status,
            idempotency_key=idempotency_key,
            human_review_required=True,
        )

    return _factory


@pytest.fixture
def make_outbox_event():
    """Factory fixture to create populated OutboxEvent model instances."""

    def _factory(
        *,
        tenant_id: str = "tenant_a",
        event_type: str = "screening.requested",
        screening_id: uuid.UUID | str | None = None,
        status: str = "pending",
        attempt_count: int = 0,
        locked_at: datetime | None = None,
        available_at: datetime | None = None,
    ) -> OutboxEvent:
        sid = str(screening_id or uuid.uuid4())
        return OutboxEvent(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            event_type=event_type,
            aggregate_id=sid,
            payload={
                "screening_id": sid,
                "enqueued_at": datetime.now(UTC).isoformat(),
            },
            status=status,
            attempt_count=attempt_count,
            locked_at=locked_at,
            available_at=available_at or datetime.now(UTC),
        )

    return _factory
