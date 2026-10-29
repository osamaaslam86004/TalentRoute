"""Shared test fixtures for CLI script integration tests."""

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base


@pytest_asyncio.fixture(scope="session")
async def async_engine():
    """Create an in-memory SQLite engine with StaticPool for session isolation."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(async_engine, monkeypatch):
    """Yield an async session and rollback changes after each test."""
    testing_session_local = async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", testing_session_local)
    monkeypatch.setattr(
        "app.scripts.requeue_dead_outbox.AsyncSessionLocal", testing_session_local
    )

    async with testing_session_local() as session:
        yield session
        await session.rollback()
