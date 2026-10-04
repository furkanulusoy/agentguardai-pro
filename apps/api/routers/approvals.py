"""Tenant and actor scoped approval metadata, human payload review and atomic resolution."""

import json
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import selectinload

from apps.api.dependencies import Actor, DbSession, require_actor_permission, require_permission
from apps.api.services.approvals import expire_stale_approvals
from apps.api.services.operations import expire_operations, run_operation
from apps.api.services.privacy import safe_context, safe_error, safe_result
from apps.api.services.security_events import record_security_event
from infrastructure.database.models import User
from infrastructure.database.models.approval import ApprovalRequest
from infrastructure.database.models.operation import Operation

router = APIRouter(prefix="/approvals", tags=["approvals"])

RequireApprovalRead = Annotated[Actor, Depends(require_actor_permission("approval.read"))]
RequireApprovalApprove = Annotated[User, Depends(require_permission("approval.approve"))]

_LOAD_OPTIONS = (
    selectinload(ApprovalRequest.credential),
    selectinload(ApprovalRequest.requested_by),
    selectinload(ApprovalRequest.resolved_by),
    selectinload(ApprovalRequest.agent),
)


class ApprovalResponse(BaseModel):
    id: uuid.UUID
    credential_id: uuid.UUID
    connector_type: str
    action: str
    call_context: dict[str, Any]
    risk_level: str
    status: str
    requested_by_email: str
    # Which Agent (infrastructure/database/models/agent.py) actually
    # called execute, if any -- None means a human called it directly.
    # requested_by_email above is always populated either way (see
    # ApprovalRequest.agent_id's own comment for why).
    agent_name: str | None
    resolved_by_email: str | None
    created_at: datetime
    resolved_at: datetime | None
    result: Any = None
    error: str | None = None
    execution_status: str | None = None
    operation_id: uuid.UUID | None = None


class ResolveRequest(BaseModel):
    payload_hash: str | None = None
    approved: bool
    model_config = {"strict": True, "extra": "forbid"}


def _to_response(request: ApprovalRequest) -> ApprovalResponse:
    return ApprovalResponse(
        id=request.id,
        credential_id=request.credential_id,
        connector_type=request.credential.connector_type,
        action=request.action,
        call_context=safe_context(request.call_context),
        risk_level=request.risk_level,
        status=request.status,
        requested_by_email=request.requested_by.email,
        agent_name=request.agent.name if request.agent else None,
        resolved_by_email=request.resolved_by.email if request.resolved_by else None,
        created_at=request.created_at,
        resolved_at=request.resolved_at,
        result=safe_result(request.result_json),
        error=safe_error(request.error_message),
    )


@router.get("", response_model=list[ApprovalResponse])
async def list_approvals(actor: RequireApprovalRead, session: DbSession) -> list[ApprovalResponse]:
    await expire_stale_approvals(session, actor.user.tenant_id)
    await expire_operations(session, actor.user.tenant_id)
    query = (
        select(ApprovalRequest)
        .options(*_LOAD_OPTIONS)
        .where(ApprovalRequest.tenant_id == actor.user.tenant_id)
    )
    if actor.agent is not None:
        # An agent may poll the approval it created, but must not learn the
        # action parameters, results, or approval decisions of other agents
        # in the same tenant.  Human approval readers keep the tenant-wide
        # operational view.
        query = query.where(ApprovalRequest.agent_id == actor.agent.id)
    result = await session.execute(query.order_by(ApprovalRequest.created_at.desc()).limit(50))
    rows = result.scalars().all()
    operations = (
        (
            await session.execute(
                select(Operation).where(Operation.approval_id.in_([r.id for r in rows]))
            )
        )
        .scalars()
        .all()
    )
    lookup = {o.approval_id: o for o in operations}
    responses = []
    for row in rows:
        response = _to_response(row)
        if row.id in lookup:
            response.execution_status = lookup[row.id].status
            response.operation_id = lookup[row.id].id
        responses.append(response)
    return responses


@router.get("/{approval_id}", response_model=ApprovalResponse)
async def get_approval(
    approval_id: uuid.UUID, actor: RequireApprovalRead, session: DbSession
) -> ApprovalResponse:
    await expire_stale_approvals(session, actor.user.tenant_id)
    await expire_operations(session, actor.user.tenant_id)
    query = (
        select(ApprovalRequest)
        .options(*_LOAD_OPTIONS)
        .where(
            ApprovalRequest.id == approval_id,
            ApprovalRequest.tenant_id == actor.user.tenant_id,
        )
    )
    if actor.agent is not None:
        query = query.where(ApprovalRequest.agent_id == actor.agent.id)
    result = await session.execute(query)
    request = result.scalar_one_or_none()
    if request is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval request not found")
    response = _to_response(request)
    operation = (
        await session.execute(select(Operation).where(Operation.approval_id == request.id))
    ).scalar_one_or_none()
    if operation:
        response.execution_status = operation.status
        response.operation_id = operation.id
    return response


@router.post("/{approval_id}/resolve", response_model=ApprovalResponse)
async def resolve_approval(
    approval_id: uuid.UUID,
    body: ResolveRequest,
    current_user: RequireApprovalApprove,
    session: DbSession,
):
    tenant_id = current_user.tenant_id
    await expire_stale_approvals(session, tenant_id)
    operation_check = (
        await session.execute(
            select(Operation).where(
                Operation.approval_id == approval_id, Operation.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if (
        body.approved
        and operation_check is not None
        and body.payload_hash != operation_check.payload_hash
    ):
        raise HTTPException(409, "Review the current payload before approval")
    claim = await session.execute(
        update(ApprovalRequest)
        .where(
            ApprovalRequest.id == approval_id,
            ApprovalRequest.tenant_id == tenant_id,
            ApprovalRequest.status == "PENDING",
        )
        .values(
            status="APPROVED" if body.approved else "DENIED",
            resolved_by_user_id=current_user.id,
            resolved_at=datetime.now(timezone.utc),
        )
    )
    if cast(CursorResult, claim).rowcount != 1:
        await session.rollback()
        exists = (
            await session.execute(
                select(ApprovalRequest.id).where(
                    ApprovalRequest.id == approval_id, ApprovalRequest.tenant_id == tenant_id
                )
            )
        ).scalar_one_or_none()
        raise HTTPException(409 if exists else 404, "Already resolved or not found")
    operation = (
        await session.execute(select(Operation).where(Operation.approval_id == approval_id))
    ).scalar_one_or_none()
    record_security_event(
        session, current_user, "approval_approve" if body.approved else "approval_deny", approval_id
    )
    if operation is None:
        # Legacy requests have no encrypted payload/decision snapshot. Never execute them blindly.
        row = await session.get(ApprovalRequest, approval_id)
        assert row is not None
        row.error_message = "LEGACY_REQUEST_RESUBMIT_REQUIRED" if body.approved else None
        await session.commit()
    elif body.approved:
        await run_operation(session, operation)
    else:
        operation.status = "DENIED"
        operation.error_code = "HUMAN_DENIED"
        await session.commit()
    result = await session.execute(
        select(ApprovalRequest)
        .options(*_LOAD_OPTIONS)
        .where(ApprovalRequest.id == approval_id)
        .execution_options(populate_existing=True)
    )
    row = result.scalar_one()
    response = _to_response(row)
    response.execution_status = operation.status if operation else None
    response.operation_id = operation.id if operation else None
    return response


@router.get("/{approval_id}/review")
async def review_approval(
    approval_id: uuid.UUID, current_user: RequireApprovalApprove, session: DbSession
):
    operation = (
        await session.execute(
            select(Operation).where(
                Operation.approval_id == approval_id, Operation.tenant_id == current_user.tenant_id
            )
        )
    ).scalar_one_or_none()
    if operation is None:
        raise HTTPException(404, "Review payload not found; resubmit legacy requests")
    from infrastructure.secrets import secret_store

    record_security_event(session, current_user, "approval_payload_review", approval_id)
    await session.commit()
    return {
        "params": json.loads(secret_store.decrypt(operation.encrypted_payload)),
        "payload_hash": operation.payload_hash,
        "policy_fingerprint": operation.policy_fingerprint,
        "decision_source": operation.decision_source,
    }
