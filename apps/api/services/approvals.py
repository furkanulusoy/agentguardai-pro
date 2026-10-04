"""Tenant-scoped lazy expiry. Expiry never releases a claimed execution."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from infrastructure.database.models.approval import ApprovalRequest

EXPIRY_MINUTES = 30


async def expire_stale_approvals(session, tenant_id):
    now = datetime.now(timezone.utc)
    await session.execute(
        update(ApprovalRequest)
        .where(
            ApprovalRequest.tenant_id == tenant_id,
            ApprovalRequest.status == "PENDING",
            ApprovalRequest.created_at < now - timedelta(minutes=EXPIRY_MINUTES),
        )
        .values(status="EXPIRED", resolved_at=now)
    )
    await session.commit()
