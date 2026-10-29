"""Shared async database session fixtures and principals for service integration tests."""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.security import Principal
from app.db.base import Base


@pytest_asyncio.fixture(scope="function")
async def async_engine():
    """Create an isolated in-memory SQLite engine with PRAGMA foreign keys enabled."""
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


@pytest_asyncio.fixture(scope="function")
async def db_session(async_engine):
    """Yield a functional database session bound to the in-memory engine."""
    session_factory = async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session


@pytest.fixture
def session_factory(async_engine):
    """Return a sessionmaker factory for services that instantiate their own sessions."""
    return async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


@pytest.fixture
def principal_tenant_a():
    """Provide a test principal for Tenant A."""
    return Principal(
        subject="reviewer-a-001",
        tenant_id="tenant_a",
        roles=frozenset({"reviewer", "recruiter"}),
        scopes=frozenset({"review:write", "screening:read"}),
        auth_method="oidc",
    )


@pytest.fixture
def principal_tenant_b():
    """Provide a test principal for Tenant B."""
    return Principal(
        subject="reviewer-b-999",
        tenant_id="tenant_b",
        roles=frozenset({"reviewer"}),
        scopes=frozenset({"review:write"}),
        auth_method="oidc",
    )


@pytest.fixture
def compliance_principal_tenant_a():
    """Provide a compliance principal for appeals in Tenant A."""
    return Principal(
        subject="compliance-a-100",
        tenant_id="tenant_a",
        roles=frozenset({"compliance_officer"}),
        scopes=frozenset({"review:appeal"}),
        auth_method="oidc",
    )
