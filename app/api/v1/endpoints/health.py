import logging

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.core.rate_limit import get_rate_limit_redis_client
from app.db.session import engine

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


@router.get("/health/live", status_code=status.HTTP_200_OK)
async def liveness() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/health/ready")
async def readiness(response: Response) -> dict[str, str]:
    components = {"database": "ok", "redis": "ok"}
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:
        components["database"] = "unavailable"
        logger.warning(
            "readiness dependency failed component=database error_type=%s",
            type(exc).__name__,
        )
    try:
        client = get_rate_limit_redis_client()
        await client.ping()
    except Exception as exc:
        components["redis"] = "unavailable"
        logger.warning(
            "readiness dependency failed component=redis error_type=%s",
            type(exc).__name__,
        )
    if any(value != "ok" for value in components.values()):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready", **components}
    return {"status": "ready", **components}
