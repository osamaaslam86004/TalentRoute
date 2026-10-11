"""Redis-backed tenant/actor rate limiting for expensive screening submissions."""

from __future__ import annotations

import hashlib
import logging
from functools import lru_cache

from fastapi import HTTPException, Request, status
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.core.security import Principal

logger = logging.getLogger(__name__)

# Lua makes increment + initial TTL assignment atomic across API replicas.
_INCREMENT_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
local ttl = redis.call('TTL', KEYS[1])
return {count, ttl}
"""


@lru_cache(maxsize=4)
def _redis_client(url: str) -> Redis:
    return Redis.from_url(
        url, decode_responses=True, socket_connect_timeout=2, socket_timeout=2
    )


async def enforce_screening_rate_limit(request: Request, principal: Principal) -> None:
    """Raise 429 when a tenant/actor exceeds its configured submission window.

    The identity material is hashed before becoming a Redis key. On Redis failure,
    behavior is configurable; the default is fail-closed with a 503 response.
    """
    settings = get_settings()

    identity = f"{principal.tenant_id}:{principal.subject}:{request.url.path}"

    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    key = f"rate-limit:screening-create:{digest}"

    try:
        result = await _redis_client(settings.redis_url).eval(
            _INCREMENT_SCRIPT, 1, key, settings.screening_rate_limit_window_seconds
        )
        count, ttl = int(result[0]), max(1, int(result[1]))

    except (RedisError, OSError, TimeoutError) as exc:
        logger.warning(
            "rate limiter backend unavailable error_type=%s", type(exc).__name__
        )

        if settings.rate_limit_fail_open:
            return

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Submission rate limiter is temporarily unavailable",
            headers={"Retry-After": "5"},
        ) from exc

    if count > settings.screening_rate_limit_requests:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Screening submission rate limit exceeded",
            headers={"Retry-After": str(ttl)},
        )


async def close_rate_limit_client() -> None:
    """Close the shared Redis connection pool during application shutdown."""

    settings = get_settings()

    client = _redis_client(settings.redis_url)

    await client.aclose()

    _redis_client.cache_clear()
