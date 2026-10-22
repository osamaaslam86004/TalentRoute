"""SQLAlchemy connection pool telemetry listeners."""

from __future__ import annotations

import logging
import time

from sqlalchemy import event

from app.observability.metrics import _record_duration

logger = logging.getLogger(__name__)


def register_db_pool_listeners(engine) -> None:
    """Register checkout/checkin/connect listeners to measure pool wait times and metrics."""

    @event.listens_for(engine.sync_engine, "checkout")
    def on_checkout(dbapi_connection, connection_record, connection_proxy) -> None:
        connection_record.info["checkout_time"] = time.perf_counter()

    @event.listens_for(engine.sync_engine, "checkin")
    def on_checkin(dbapi_connection, connection_record) -> None:
        checkout_time = connection_record.info.pop("checkout_time", None)
        if checkout_time is not None:
            duration = time.perf_counter() - checkout_time
            logger.debug("db connection checkout duration_ms=%.2f", duration * 1000)

    @event.listens_for(engine.sync_engine, "connect")
    def on_connect(dbapi_connection, connection_record) -> None:
        logger.debug("db connection established")
