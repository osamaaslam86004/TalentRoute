"""Observability for the screening service: structured logs, correlation context and privacy policy.

Dependency rule: domain code may import from here; this package imports only ``app.core.config``.
Telemetry is best-effort and strictly separate from the hash-chained audit trail in ``app.audit``.
"""

from __future__ import annotations

from typing import Literal

from app.core.config import Settings, get_settings
from app.observability.logging import configure_logging
from app.observability.policy import enforce_langsmith_policy

Role = Literal["api", "worker", "beat", "cli"]

__all__ = ["Role", "setup_observability"]


def setup_observability(role: Role = "api", settings: Settings | None = None) -> None:
    """Initialise telemetry for one process. Safe to call more than once.

    Order matters: the policy check runs first so a misconfigured production process fails before
    it logs or serves anything.
    """

    settings = settings or get_settings()
    enforce_langsmith_policy(settings)
    configure_logging(settings, role=role)
