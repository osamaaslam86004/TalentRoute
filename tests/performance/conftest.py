"""Shared performance test fixtures, async database session factories, and auth principals."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import StaticPool
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings, get_settings
from app.core.security import Principal
from app.db.base import Base
from app.db.session import get_db_session
from app.main import app

# Performance test database engine with StaticPool for in-memory SQLite async concurrency
PERF_TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def perf_async_engine() -> AsyncGenerator[AsyncEngine, None]:
    """Provide a session-scoped async SQLAlchemy engine configured for performance testing."""
    engine = create_async_engine(
        PERF_TEST_DB_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def perf_session_factory(
    perf_async_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """Provide a session factory for creating independent concurrent database sessions."""
    return async_sessionmaker(
        bind=perf_async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


@pytest_asyncio.fixture(scope="function")
async def perf_db_session(
    perf_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """Provide an isolated database session for performance test setups."""
    async with perf_session_factory() as session:
        yield session


@pytest.fixture
def perf_settings() -> Settings:
    """Return test runtime settings."""
    return get_settings()


@pytest.fixture
def tenant_a() -> str:
    """Return primary test tenant ID."""
    return "perf-tenant-alpha"


@pytest.fixture
def tenant_b() -> str:
    """Return secondary test tenant ID for isolation tests."""
    return "perf-tenant-beta"


@pytest.fixture
def principal_recruiter_tenant_a(tenant_a: str) -> Principal:
    """Return recruiter principal for Tenant A."""
    return Principal(
        subject="recruiter-perf-01",
        tenant_id=tenant_a,
        roles=frozenset({"recruiter"}),
        scopes=frozenset({"screening:write", "screening:read", "review:read"}),
        auth_method="oidc",
    )


@pytest.fixture
def principal_auditor_tenant_a(tenant_a: str) -> Principal:
    """Return auditor principal for Tenant A."""
    return Principal(
        subject="auditor-perf-01",
        tenant_id=tenant_a,
        roles=frozenset({"auditor"}),
        scopes=frozenset({"audit:read", "screening:read", "review:read"}),
        auth_method="oidc",
    )


@pytest_asyncio.fixture(scope="function")
async def perf_api_client(
    perf_session_factory: async_sessionmaker[AsyncSession],
    principal_recruiter_tenant_a: Principal,
) -> AsyncGenerator[AsyncClient, None]:
    """Provide an HTTP async test client with database session and auth dependency overrides."""

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with perf_session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"X-API-Key": "dev-key"},
    ) as client:
        yield client

    app.dependency_overrides.clear()
