"""Canonical hashing and verification for per-tenant, tamper-evident audit event chains."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

GENESIS_HASH = "0" * 64


def _timestamp(value: datetime) -> str:

    if value.tzinfo is None:
        raise ValueError("Audit timestamps must be timezone-aware")

    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def audit_hash_payload(
    *,
    event_id: str,
    occurred_at: datetime,
    tenant_id: str,
    actor_subject: str,
    actor_roles: list[str],
    request_id: str | None,
    action: str,
    resource_type: str,
    resource_id: str | None,
    outcome: str,
    metadata: dict[str, Any],
    sequence_no: int,
    previous_hash: str,
) -> dict[str, Any]:

    return {
        "id": event_id,
        "occurred_at": _timestamp(occurred_at),
        "tenant_id": tenant_id,
        "actor_subject": actor_subject,
        "actor_roles": sorted(actor_roles),
        "request_id": request_id,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "outcome": outcome,
        "metadata": metadata,
        "sequence_no": sequence_no,
        "previous_hash": previous_hash,
    }


def calculate_event_hash(payload: dict[str, Any]) -> str:

    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

    return hashlib.sha256(canonical).hexdigest()
