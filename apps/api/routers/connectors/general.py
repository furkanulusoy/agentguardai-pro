"""Scoped connector discovery, revocation and durable execution submission."""

import json
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from apps.api.dependencies import Actor, DbSession, require_actor_permission, require_permission
from apps.api.services.governance import authorize
from apps.api.services.governance_lock import lock_governance
from apps.api.services.operations import expire_operations, operation_response, run_operation
from apps.api.services.privacy import audit_context, fingerprint
from apps.api.services.security_events import record_security_event
from infrastructure.database.models import AgentCredentialGrant, Credential, User
from infrastructure.database.models.approval import ApprovalRequest
from infrastructure.database.models.audit_event import AuditEvent
from infrastructure.database.models.operation import Operation
from infrastructure.secrets import secret_store

router = APIRouter(prefix="/connectors", tags=["connectors"])

RequireConnectorRead = Annotated[Actor, Depends(require_actor_permission("connector.read"))]
RequireConnectorWrite = Annotated[User, Depends(require_permission("connector.write"))]
RequireAgentExecute = Annotated[Actor, Depends(require_actor_permission("agent.execute"))]


class ConnectorResponse(BaseModel):
    id: uuid.UUID
    connector_type: str
    label: str
    connected_at: datetime
    is_revoked: bool


@router.get("", response_model=list[ConnectorResponse])
async def list_connectors(
    actor: RequireConnectorRead, session: DbSession
) -> list[ConnectorResponse]:
    # Actor-based, like execute_connector_action -- an Agent (Phase B)
    # needs to see what it can act on before calling execute, same as a
    # human does via the dashboard. connector.write (revoke) stays
    # human-only: an agent discovering its own connectors is a read,
    # revoking one is an admin action, not something an agent calls
    # itself.
    current_user = actor.user
    query = select(Credential).where(Credential.tenant_id == current_user.tenant_id)
    if actor.agent is not None:
        # An AgentCredentialGrant is not merely an execution gate.  Returning
        # every credential in the tenant here would let an otherwise
        # constrained agent discover other agents' integrations and labels.
        # Human callers retain the tenant-wide dashboard view; an agent sees
        # only the credentials it was explicitly granted.
        query = query.join(
            AgentCredentialGrant,
            AgentCredentialGrant.credential_id == Credential.id,
        ).where(
            AgentCredentialGrant.agent_id == actor.agent.id,
            Credential.revoked_at.is_(None),
        )
    result = await session.execute(query.order_by(Credential.created_at.desc()))
    return [
        ConnectorResponse(
            id=c.id,
            connector_type=c.connector_type,
            label=c.label,
            connected_at=c.created_at,
            is_revoked=c.revoked_at is not None,
        )
        for c in result.scalars().all()
    ]


@router.post("/{credential_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_connector(
    credential_id: uuid.UUID, current_user: RequireConnectorWrite, session: DbSession
) -> None:
    await lock_governance(session, current_user.tenant_id)
    credential = await session.get(Credential, credential_id)
    if credential is None or credential.tenant_id != current_user.tenant_id:
        # Same 404 whether it doesn't exist or belongs to another tenant --
        # a caller must not be able to tell those apart (matches the
        # tenant-isolation discipline everywhere else in this project).
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connector not found")
    if credential.revoked_at is None:
        credential.revoked_at = datetime.now(timezone.utc)
        record_security_event(session, current_user, "revoke_connector", credential_id)
        await session.commit()


class ExecuteRequest(BaseModel):
    action: str = Field(min_length=1, max_length=100, pattern=r"^[a-z_]+$")
    params: dict[str, Any] = Field(default_factory=dict)
    model_config = {"extra": "forbid"}


@router.post("/{credential_id}/execute")
async def execute_connector_action(
    credential_id: uuid.UUID,
    body: ExecuteRequest,
    actor: RequireAgentExecute,
    session: DbSession,
    idempotency_key: Annotated[str, Header(min_length=16, max_length=128)],
):
    user = actor.user
    credential = (
        await session.execute(
            select(Credential).where(
                Credential.id == credential_id,
                Credential.tenant_id == user.tenant_id,
                Credential.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if credential is None:
        raise HTTPException(404, "Connector not found")
    actor_key = f"agent:{actor.agent.id}" if actor.agent else f"user:{user.id}"
    payload_hash = fingerprint(
        {"credential": str(credential_id), "action": body.action, "params": body.params}
    )
    query = select(Operation).where(
        Operation.tenant_id == user.tenant_id,
        Operation.actor_key == actor_key,
        Operation.idempotency_key == idempotency_key,
    )
    existing = (await session.execute(query)).scalar_one_or_none()
    if existing:
        if existing.payload_hash != payload_hash:
            raise HTTPException(409, "Idempotency key was already used for a different payload")
        current_decision = await authorize(
            session,
            user_id=user.id,
            agent_id=actor.agent.id if actor.agent else None,
            credential=credential,
            action=body.action,
            params=body.params,
        )
        if current_decision.decision == "DENY":
            raise HTTPException(403, "Operation access is no longer authorized")
        return operation_response(existing)
    decision = await authorize(
        session,
        user_id=user.id,
        agent_id=actor.agent.id if actor.agent else None,
        credential=credential,
        action=body.action,
        params=body.params,
    )
    audit = AuditEvent(
        tenant_id=user.tenant_id,
        credential_id=credential.id,
        user_id=user.id,
        agent_id=actor.agent.id if actor.agent else None,
        action=body.action,
        call_context=audit_context(body.params),
        decision=decision.decision,
        decision_source=decision.source,
        risk_level=decision.risk,
    )
    session.add(audit)
    if decision.decision == "DENY":
        await session.commit()
        raise HTTPException(403, decision.source)
    operation = Operation(
        tenant_id=user.tenant_id,
        user_id=user.id,
        agent_id=actor.agent.id if actor.agent else None,
        credential_id=credential.id,
        actor_key=actor_key,
        idempotency_key=idempotency_key,
        payload_hash=payload_hash,
        encrypted_payload=secret_store.encrypt(json.dumps(decision.params)),
        action=body.action,
        status="WAITING" if decision.decision == "REQUIRE_APPROVAL" else "READY",
        decision_source=decision.source,
        policy_fingerprint=decision.policy_hash,
    )
    try:
        await session.flush()
        operation.audit_id = audit.id
        if decision.decision == "REQUIRE_APPROVAL":
            approval = ApprovalRequest(
                tenant_id=user.tenant_id,
                credential_id=credential.id,
                requested_by_user_id=user.id,
                agent_id=actor.agent.id if actor.agent else None,
                action=body.action,
                call_context=audit_context(decision.params),
                risk_level=decision.risk,
                status="PENDING",
            )
            session.add(approval)
            await session.flush()
            operation.approval_id = approval.id
            audit.approval_id = approval.id
        session.add(operation)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = (await session.execute(query)).scalar_one_or_none()
        if existing is None or existing.payload_hash != payload_hash:
            raise HTTPException(409, "Concurrent request conflict") from None
        return operation_response(existing)
    if operation.status == "READY":
        await run_operation(session, operation)
    return operation_response(operation)


@router.get("/operations/{operation_id}")
async def get_operation(operation_id: uuid.UUID, actor: RequireAgentExecute, session: DbSession):
    await expire_operations(session, actor.user.tenant_id)
    actor_key = f"agent:{actor.agent.id}" if actor.agent else f"user:{actor.user.id}"
    operation = (
        await session.execute(
            select(Operation).where(
                Operation.id == operation_id,
                Operation.tenant_id == actor.user.tenant_id,
                Operation.actor_key == actor_key,
            )
        )
    ).scalar_one_or_none()
    if operation is None:
        raise HTTPException(404, "Operation not found")
    credential = await session.get(Credential, operation.credential_id, populate_existing=True)
    current_decision = await authorize(
        session,
        user_id=actor.user.id,
        agent_id=actor.agent.id if actor.agent else None,
        credential=credential,
        action=operation.action,
        params=json.loads(secret_store.decrypt(operation.encrypted_payload)),
    )
    if current_decision.decision == "DENY":
        raise HTTPException(403, "Operation access is no longer authorized")
    return operation_response(operation)
