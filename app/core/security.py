import hmac

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.core.config import get_settings

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(provided_key: str | None = Security(_api_key_header)) -> None:
    settings = get_settings()
    expected = settings.api_key.get_secret_value() if settings.api_key else None
    if expected is None:
        if settings.environment in {"development", "test"}:
            return
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API authentication is not configured",
        )
    if not provided_key or not hmac.compare_digest(provided_key, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key")
