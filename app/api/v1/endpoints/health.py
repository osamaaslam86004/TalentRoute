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
        engine_obj = engine
        if engine_obj is None:
            raise TypeError("engine is not bound")
        if not hasattr(engine_obj, "connect"):
            raise TypeError("engine does not provide connect")
        connect_result = engine_obj.connect()
        if hasattr(connect_result, "__aenter__"):
            async with connect_result as connection:
                await connection.execute(text("SELECT 1"))
        elif hasattr(connect_result, "__await__"):
            connection = await connect_result
            async with connection as conn:
                await conn.execute(text("SELECT 1"))
        else:
            raise TypeError("engine connect did not yield an async context manager")
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
