import asyncio
import time

import pytest

from app.audit.service import record_audit_event
from app.core.security import Principal


@pytest.mark.asyncio
async def test_audit_chain_head_lock_contention(perf_session_factory, tenant_a: str):
    """Concurrently invoke record_audit_event across 2 workers for the same tenant."""
    concurrency_count = 2
    durations: list[float] = []

    async def _audit_worker(worker_idx: int) -> None:
        principal = Principal(
            subject=f"worker-{worker_idx:02d}",
            tenant_id=tenant_a,
            roles=frozenset({"service"}),
            scopes=frozenset({"screening:write"}),
            auth_method="oidc",
        )
        start_time = time.perf_counter()
        async with perf_session_factory() as session:
            await record_audit_event(
                session,
                principal=principal,
                tenant_id=tenant_a,
                request_id=f"req-perf-{worker_idx}",
                action="screening.concurrent_test",
                resource_type="screening",
                resource_id=f"res-{worker_idx}",
                metadata={"status": "queued", "count": worker_idx},
            )
            await session.commit()
        durations.append(time.perf_counter() - start_time)

    # Launch concurrent workers recording audit events for the same tenant
    tasks = [_audit_worker(i) for i in range(concurrency_count)]
    await asyncio.gather(*tasks)
