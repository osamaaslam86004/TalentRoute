import logging
import os


def configure_logging() -> None:
    """Configure concise process-wide logs; never log prompts or application text."""
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
