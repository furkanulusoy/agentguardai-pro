"""
GET /tenant/audit -- the real audit trail Phase C (docs/PRODUCTIZATION_ROADMAP.md)
promised: every governance decision POST /connectors/{id}/execute makes,
not just the ones that happened to need a human's approval. See
infrastructure/database/models/audit_event.py's own docstring for what
each row records and why.

Gated behind approval.read, the same permission that already governs
seeing ApprovalRequest.call_context (apps/api/routers/approvals.py) --
an AuditEvent carries the identical informational-only call_context, so
whoever could already see one kind of "what did an agent try to do"
record should see the other.

For a REQUIRE_APPROVAL row, the eventual outcome (approved/denied/
expired, result/error) is resolved here by joining the linked
ApprovalRequest at read time -- deliberately not duplicated onto
AuditEvent itself, so there is exactly one place that state can live.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from apps.api.dependencies import DbSession, require_permission
from apps.api.services.privacy import safe_context, safe_error, safe_result
from infrastructure.database.models import User
from infrastructure.database.models.audit_event import AuditEvent

router = APIRouter(prefix="/tenant/audit", tags=["audit"])

RequireApprovalRead = Annotated[User, Depends(require_permission("approval.read"))]

_LOAD_OPTIONS = (
    selectinload(AuditEvent.credential),
    selectinload(AuditEvent.user),
    selectinload(AuditEvent.agent),
    selectinload(AuditEvent.approval),
)


class AuditEventResponse(BaseModel):
    id: uuid.UUID
    created_at: datetime
    connector_type: str
    action: str
    call_context: dict[str, Any]
    user_email: str
    agent_name: str | None
    decision: str
    decision_source: str
    risk_level: str
    # Populated only for a decision == "ALLOW" row -- it ran
    # synchronously, so this IS the outcome (see AuditEvent's docstring).
    execution_status: str | None = None
    result: Any = None
    error: str | None = None
    # Populated only for a decision == "REQUIRE_APPROVAL" row, resolved
    # live from the linked ApprovalRequest -- None here just means "still
    # pending," not "nothing happened."
    approval_id: uuid.UUID | None = None
    approval_status: str | None = None
    approval_resolved_by_email: str | None = None


def _to_response(event: AuditEvent) -> AuditEventResponse:
    approval = event.approval
    return AuditEventResponse(
        id=event.id,
        created_at=event.created_at,
        connector_type=event.credential.connector_type,
        action=event.action,
        call_context=safe_context(event.call_context),
        user_email=event.user.email,
        agent_name=event.agent.name if event.agent else None,
        decision=event.decision,
        decision_source=event.decision_source,
        risk_level=event.risk_level,
        result=safe_result(event.result_json),
        error=safe_error(event.error_message),
        approval_id=event.approval_id,
        approval_status=approval.status if approval else None,
        approval_resolved_by_email=(
            approval.resolved_by.email if approval and approval.resolved_by else None
        ),
    )


@router.get("", response_model=list[AuditEventResponse])
async def list_audit_events(
    current_user: RequireApprovalRead,
    session: DbSession,
    connector_type: Annotated[str | None, Query()] = None,
    action: Annotated[str | None, Query()] = None,
    decision: Annotated[str | None, Query()] = None,
    agent_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[AuditEventResponse]:
    query = (
        select(AuditEvent)
        .options(*_LOAD_OPTIONS)
        .where(AuditEvent.tenant_id == current_user.tenant_id)
    )
    if connector_type:
        query = query.where(AuditEvent.credential.has(connector_type=connector_type))
    if action:
        query = query.where(AuditEvent.action == action)
    if decision:
        query = query.where(AuditEvent.decision == decision)
    if agent_id:
        query = query.where(AuditEvent.agent_id == agent_id)

    result = await session.execute(query.order_by(AuditEvent.created_at.desc()).limit(limit))
    rows = result.scalars().all()
    from infrastructure.database.models import Operation

    operations = (
        (
            await session.execute(
                select(Operation).where(
                    Operation.tenant_id == current_user.tenant_id,
                    Operation.audit_id.in_([e.id for e in rows]),
                )
            )
        )
        .scalars()
        .all()
    )
    states = {o.audit_id: o.status for o in operations}
    responses = []
    for event in rows:
        response = _to_response(event)
        response.execution_status = states.get(event.id)
        responses.append(response)
    return responses


@router.get("/management")
async def management_audit(
    current_user: Annotated[User, Depends(require_permission("tenant.admin"))],
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
):
    from infrastructure.database.models import SecurityEvent

    rows = (
        (
            await session.execute(
                select(SecurityEvent)
                .where(SecurityEvent.tenant_id == current_user.tenant_id)
                .order_by(SecurityEvent.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(row.id),
            "created_at": row.created_at,
            "actor_id": str(row.actor_id) if row.actor_id else None,
            "action": row.action,
            "target": row.target,
        }
        for row in rows
    ]
