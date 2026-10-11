from fastapi import APIRouter, status
from redis.asyncio import from_url
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import engine

router = APIRouter(tags=["health"])


@router.get("/health/live", status_code=status.HTTP_200_OK)
async def liveness() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/health/ready")
async def readiness() -> dict[str, str]:
    settings = get_settings()

    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))

    client = from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)

    try:
        await client.ping()
    finally:
        await client.aclose()

    return {"status": "ready", "database": "ok", "redis": "ok"}
