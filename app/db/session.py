from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.observability.db_pool import register_db_pool_listeners

settings = get_settings()


def create_worker_engine(database_url: str | None = None) -> AsyncEngine:
    """Create an async SQLAlchemy engine using NullPool for Celery workers to avoid event loop binding errors."""
    return create_async_engine(
        database_url or settings.database_url,
        poolclass=NullPool,
    )


engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    pool_recycle=1800,
)

register_db_pool_listeners(engine)

AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

worker_engine = create_worker_engine()
WorkerAsyncSessionLocal = async_sessionmaker(
    worker_engine, class_=AsyncSession, expire_on_commit=False
)


def configure_worker_db() -> None:
    """Update database session and engine configuration to use NullPool for Celery workers."""
    global engine, AsyncSessionLocal
    engine = worker_engine
    AsyncSessionLocal.configure(bind=worker_engine)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


async def get_worker_session() -> AsyncGenerator[AsyncSession, None]:
    async with WorkerAsyncSessionLocal() as session:
        yield session
