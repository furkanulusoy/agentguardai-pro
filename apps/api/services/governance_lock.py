"""Serialize authorization changes with execution claims; lock is released before external I/O."""

from sqlalchemy import select

from infrastructure.database.models import Tenant


async def lock_governance(session, tenant_id):
    await session.execute(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update())
