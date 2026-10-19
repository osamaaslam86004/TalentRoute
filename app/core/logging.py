"""Compatibility shim: logging now lives in ``app.observability``."""

from app.observability import setup_observability


def configure_logging() -> None:
    """Configure process-wide logs; never log prompts or application text."""

    setup_observability(role="api")
