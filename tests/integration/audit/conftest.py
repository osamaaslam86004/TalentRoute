"""Fixtures for app/audit integration tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.core.security import Principal
from app.db.base import Base


@pytest.fixture
def principal_tenant_a() -> Principal:
    """Return a test principal for Tenant A."""
    return Principal(
        subject="user-auditor-a",
        tenant_id="tenant-a",
        roles=frozenset({"auditor", "recruiter"}),
        scopes=frozenset({"audit:read", "screening:read"}),
        auth_method="oidc",
    )


@pytest.fixture
def principal_tenant_b() -> Principal:
    """Return a test principal for Tenant B."""
    return Principal(
        subject="user-auditor-b",
        tenant_id="tenant-b",
        roles=frozenset({"auditor"}),
        scopes=frozenset({"audit:read"}),
        auth_method="oidc",
    )


@pytest_asyncio.fixture
async def async_engine() -> AsyncGenerator[AsyncEngine, None]:
    """Create an in-memory SQLite engine for audit integration testing."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(
    async_engine: AsyncEngine,
) -> AsyncGenerator[AsyncSession, None]:
    """Yield an AsyncSession bound to the in-memory test database."""
    session_factory = async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session
