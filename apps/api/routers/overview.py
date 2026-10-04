"""Dashboard aggregates from real tenant records; never fabricated health or risk scores."""

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from apps.api.dependencies import DbSession, require_permission
from infrastructure.config import settings
from infrastructure.database.models import (
    Agent,
    ApprovalRequest,
    AuditEvent,
    Credential,
    Operation,
    User,
)

router = APIRouter(prefix="/tenant", tags=["overview"])
Reader = Annotated[User, Depends(require_permission("approval.read"))]


@router.get("/overview")
async def overview(user: Reader, session: DbSession):
    async def count(model, *conditions):
        return (
            await session.execute(
                select(func.count())
                .select_from(model)
                .where(model.tenant_id == user.tenant_id, *conditions)
            )
        ).scalar_one()

    since = datetime.now(timezone.utc) - timedelta(hours=24)
    return {
        "active_agents": await count(Agent, Agent.revoked_at.is_(None)),
        "active_connectors": await count(Credential, Credential.revoked_at.is_(None)),
        "pending_approvals": await count(
            ApprovalRequest,
            ApprovalRequest.status == "PENDING",
            ApprovalRequest.created_at >= datetime.now(timezone.utc) - timedelta(minutes=30),
        ),
        "decisions_24h": await count(AuditEvent, AuditEvent.created_at >= since),
        "blocked_24h": await count(
            AuditEvent, AuditEvent.created_at >= since, AuditEvent.decision == "DENY"
        ),
        "succeeded_24h": await count(
            Operation, Operation.created_at >= since, Operation.status == "SUCCEEDED"
        ),
        "unknown": await count(Operation, Operation.status.in_(["UNKNOWN", "EXECUTING"])),
        "connectors": [
            {"type": "gmail", "configured": bool(settings.google_oauth_client_id), "actions": 2},
            {"type": "github", "configured": bool(settings.github_oauth_client_id), "actions": 2},
            {"type": "slack", "configured": bool(settings.slack_oauth_client_id), "actions": 5},
        ],
        "deployment_mode": settings.deployment_mode,
    }


@router.get("/operations")
async def operations(user: Reader, session: DbSession):
    from apps.api.services.operations import expire_operations

    await expire_operations(session, user.tenant_id)
    rows = (
        (
            await session.execute(
                select(Operation)
                .where(Operation.tenant_id == user.tenant_id)
                .order_by(Operation.created_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(o.id),
            "action": o.action,
            "status": o.status,
            "decision_source": o.decision_source,
            "created_at": o.created_at,
            "error": o.error_code,
            "approval_id": str(o.approval_id) if o.approval_id else None,
            "policy_fingerprint": o.policy_fingerprint,
            "payload_hash": o.payload_hash,
        }
        for o in rows
    ]
