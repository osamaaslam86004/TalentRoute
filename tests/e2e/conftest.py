"""Shared E2E test fixtures, FastAPI test client setup, and security principal overrides."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.core.security import Principal, get_principal
from app.db.base import Base
from app.db.session import get_db_session
from app.main import app


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def e2e_engine() -> AsyncGenerator[AsyncEngine, None]:
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
async def e2e_db_session(
    e2e_engine: AsyncEngine,
) -> AsyncGenerator[AsyncSession, None]:
    async_session_factory = async_sessionmaker(
        e2e_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session_factory() as session:
        yield session


@pytest.fixture
def principal_recruiter_tenant_a() -> Principal:
    return Principal(
        subject="recruiter-e2e-user-1",
        tenant_id="tenant-e2e-a",
        roles=frozenset({"recruiter"}),
        scopes=frozenset({"screening:write", "screening:read"}),
        auth_method="oidc",
    )


@pytest.fixture
def principal_reviewer_tenant_a() -> Principal:
    return Principal(
        subject="reviewer-e2e-user-1",
        tenant_id="tenant-e2e-a",
        roles=frozenset({"reviewer"}),
        scopes=frozenset({"screening:read", "review:write", "review:read"}),
        auth_method="oidc",
    )


@pytest.fixture
def principal_auditor_tenant_a() -> Principal:
    return Principal(
        subject="auditor-e2e-user-1",
        tenant_id="tenant-e2e-a",
        roles=frozenset({"auditor"}),
        scopes=frozenset({"screening:read", "review:read", "audit:read"}),
        auth_method="oidc",
    )


@pytest.fixture
def principal_privacy_officer_tenant_a() -> Principal:
    return Principal(
        subject="privacy-e2e-user-1",
        tenant_id="tenant-e2e-a",
        roles=frozenset({"privacy_officer"}),
        scopes=frozenset({"screening:read", "screening:delete"}),
        auth_method="oidc",
    )


@pytest.fixture
def principal_compliance_officer_tenant_a() -> Principal:
    return Principal(
        subject="compliance-e2e-user-1",
        tenant_id="tenant-e2e-a",
        roles=frozenset({"compliance_officer"}),
        scopes=frozenset({"review:appeal"}),
        auth_method="oidc",
    )


@pytest.fixture
def principal_tenant_b() -> Principal:
    return Principal(
        subject="recruiter-e2e-user-b",
        tenant_id="tenant-e2e-b",
        roles=frozenset({"recruiter"}),
        scopes=frozenset({"screening:write", "screening:read"}),
        auth_method="oidc",
    )


def make_client_for_principal(
    fastapi_app: FastAPI,
    session: AsyncSession,
    principal: Principal | None,
) -> AsyncClient:
    async def _override_db():
        yield session

    async def _override_principal():
        return principal

    fastapi_app.dependency_overrides[get_db_session] = _override_db
    if principal is not None:
        fastapi_app.dependency_overrides[get_principal] = _override_principal
    else:
        fastapi_app.dependency_overrides.pop(get_principal, None)

    return AsyncClient(
        transport=ASGITransport(app=fastapi_app),
        base_url="http://testserver",
    )


@pytest_asyncio.fixture
async def recruiter_client_tenant_a(
    e2e_db_session: AsyncSession, principal_recruiter_tenant_a: Principal
) -> AsyncGenerator[AsyncClient, None]:
    client = make_client_for_principal(
        app, e2e_db_session, principal_recruiter_tenant_a
    )
    async with client:
        yield client
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def reviewer_client_tenant_a(
    e2e_db_session: AsyncSession, principal_reviewer_tenant_a: Principal
) -> AsyncGenerator[AsyncClient, None]:
    client = make_client_for_principal(app, e2e_db_session, principal_reviewer_tenant_a)
    async with client:
        yield client
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def auditor_client_tenant_a(
    e2e_db_session: AsyncSession, principal_auditor_tenant_a: Principal
) -> AsyncGenerator[AsyncClient, None]:
    client = make_client_for_principal(app, e2e_db_session, principal_auditor_tenant_a)
    async with client:
        yield client
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def privacy_officer_client_tenant_a(
    e2e_db_session: AsyncSession, principal_privacy_officer_tenant_a: Principal
) -> AsyncGenerator[AsyncClient, None]:
    client = make_client_for_principal(
        app, e2e_db_session, principal_privacy_officer_tenant_a
    )
    async with client:
        yield client
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def compliance_officer_client_tenant_a(
    e2e_db_session: AsyncSession, principal_compliance_officer_tenant_a: Principal
) -> AsyncGenerator[AsyncClient, None]:
    client = make_client_for_principal(
        app, e2e_db_session, principal_compliance_officer_tenant_a
    )
    async with client:
        yield client
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def unauthenticated_client(
    e2e_db_session: AsyncSession,
) -> AsyncGenerator[AsyncClient, None]:
    client = make_client_for_principal(app, e2e_db_session, None)
    async with client:
        yield client
    app.dependency_overrides.clear()
