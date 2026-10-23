"""Unit tests for database declarative base, UUID primary keys, timestamp mixins, and tenant isolation."""
import uuid
from datetime import datetime, timezone
import pytest
from sqlalchemy import Column, String, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPrimaryKeyMixin, TenantIsolationMixin


class DummyModel(Base, UUIDPrimaryKeyMixin, TimestampMixin, TenantIsolationMixin):
    """Concrete model stub used to test structural mixins."""
    __tablename__ = "dummy_models"
    name = Column(String(100), nullable=False)


@pytest.fixture
async def in_memory_db():
    """Provide an isolated asynchronous SQLite engine and session for schema verification."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield session_maker

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_model_mixins_generation(in_memory_db):
    """Verify that UUID generation, timestamps, and tenant attributes populate correctly on commit."""
    async with in_memory_db() as session:
        dummy = DummyModel(name="Candidate Review Workflow", tenant_id="tenant_alpha_01")
        session.add(dummy)
        await session.commit()
        await session.refresh(dummy)

        # Assert UUID primary key is generated properly
        assert isinstance(dummy.id, uuid.UUID) or len(str(dummy.id)) == 36

        # Assert timestamps are set automatically
        assert isinstance(dummy.created_at, datetime)
        assert isinstance(dummy.updated_at, datetime)

        # Assert tenant isolation tag matches
        assert dummy.tenant_id == "tenant_alpha_01"


@pytest.mark.asyncio
async def test_tenant_isolation_filtering(in_memory_db):
    """Verify query isolation across separate tenants."""
    async with in_memory_db() as session:
        session.add(DummyModel(name="Record Alpha", tenant_id="tenant_alpha"))
        session.add(DummyModel(name="Record Beta", tenant_id="tenant_beta"))
        await session.commit()

        # Query filtering by tenant explicit scope
        stmt = select(DummyModel).where(DummyModel.tenant_id == "tenant_alpha")
        result = await session.execute(stmt)
        records = result.scalars().all()

        assert len(records) == 1
        assert records[0].name == "Record Alpha"