"""Recover operations that were persisted but not claimed before an API interruption.

Only READY work is eligible. EXECUTING and UNKNOWN operations may already have reached a
provider, so this worker never retries them. Stale-state maintenance converts those rows to
safe terminal states through the platform's existing operation service.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid

from sqlalchemy import distinct, select

from apps.api.services.operations import expire_operations, run_operation
from infrastructure.database.models import Operation
from infrastructure.database.session import SessionLocal

logger = logging.getLogger("agentguard.worker")


def poll_interval() -> float:
    try:
        value = float(os.environ.get("AGENTGUARD_WORKER_POLL_SECONDS", "2"))
    except ValueError:
        return 2.0
    return min(max(value, 0.25), 60.0)


async def process_one_ready(tenant_id: uuid.UUID | None = None) -> bool:
    """Attempt one READY operation using the normal authorization and atomic claim path."""
    async with SessionLocal() as session:
        query = select(Operation).where(Operation.status == "READY")
        if tenant_id is not None:
            query = query.where(Operation.tenant_id == tenant_id)
        operation = (
            await session.execute(query.order_by(Operation.created_at.asc()).limit(1))
        ).scalar_one_or_none()
        if operation is None:
            return False
        # run_operation reauthorizes first and atomically changes READY -> EXECUTING before I/O.
        # Concurrent workers may select the same row, but only the successful claim calls the
        # provider. The losing worker returns without retrying the operation.
        await run_operation(session, operation)
        return True


async def maintain_stale_states() -> None:
    """Make stale EXECUTING/WAITING states visible even when no user polls the API."""
    async with SessionLocal() as session:
        tenant_ids = (await session.execute(select(distinct(Operation.tenant_id)))).scalars().all()
    for tenant_id in tenant_ids:
        async with SessionLocal() as session:
            await expire_operations(session, tenant_id)


async def run_forever() -> None:
    interval = poll_interval()
    while True:
        try:
            processed = await process_one_ready()
            if not processed:
                await maintain_stale_states()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Do not render exception text: driver/provider errors may contain private metadata.
            logger.error("worker_cycle_failed")
            processed = False
        if not processed:
            await asyncio.sleep(interval)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
