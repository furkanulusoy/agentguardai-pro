"""Claim before I/O. Never retry unknown side effects; reconcile with the provider."""

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from starlette.concurrency import run_in_threadpool

from apps.api.services.execution import run_connector_action
from apps.api.services.governance import authorize
from apps.api.services.privacy import result_summary
from connectors.base import ConnectorAuthenticationError
from infrastructure.database.models import ApprovalRequest, AuditEvent, Credential, Operation
from infrastructure.secrets import secret_store


async def expire_operations(session, tenant_id):
    now = datetime.now(timezone.utc)
    await session.execute(
        update(Operation)
        .where(
            Operation.tenant_id == tenant_id,
            Operation.status == "EXECUTING",
            Operation.started_at < now - timedelta(minutes=5),
        )
        .values(status="UNKNOWN", error_code="EXECUTION_OUTCOME_UNKNOWN")
    )
    await session.execute(
        update(Operation)
        .where(
            Operation.tenant_id == tenant_id,
            Operation.status == "WAITING",
            Operation.approval_id.in_(
                select(ApprovalRequest.id).where(ApprovalRequest.status == "EXPIRED")
            ),
        )
        .values(status="EXPIRED", error_code="APPROVAL_EXPIRED")
    )
    await session.execute(
        update(Operation)
        .where(
            Operation.tenant_id == tenant_id,
            Operation.status == "READY",
            Operation.created_at < now - timedelta(minutes=5),
        )
        .values(status="FAILED", error_code="EXECUTION_NOT_STARTED", finished_at=now)
    )
    await session.commit()


async def run_operation(session, operation):
    credential = await session.get(Credential, operation.credential_id, populate_existing=True)
    payload = json.loads(secret_store.decrypt(operation.encrypted_payload))
    if operation.actor_key.startswith("agent:") and operation.agent_id is None:
        decision = None
    else:
        decision = await authorize(
            session,
            user_id=operation.user_id,
            agent_id=operation.agent_id,
            credential=credential,
            action=operation.action,
            params=payload,
        )
    allowed = decision is not None and decision.decision != "DENY"
    if decision is not None and decision.policy_hash != operation.policy_fingerprint:
        allowed = False
    if decision is not None and decision.decision == "REQUIRE_APPROVAL":
        approval = (
            await session.get(ApprovalRequest, operation.approval_id)
            if operation.approval_id
            else None
        )
        allowed = allowed and approval is not None and approval.status == "APPROVED"
    claimed = await session.execute(
        update(Operation)
        .where(Operation.id == operation.id, Operation.status.in_(["READY", "WAITING"]))
        .values(
            status="EXECUTING" if allowed else "DENIED",
            started_at=datetime.now(timezone.utc),
            error_code=None if allowed else "AUTHORIZATION_CHANGED",
        )
    )
    if claimed.rowcount != 1:
        await session.rollback()
        return
    # Approval decision and execution ownership become durable together, BEFORE the external call.
    await session.commit()
    await session.refresh(operation)
    if not allowed:
        if operation.approval_id:
            approval = await session.get(ApprovalRequest, operation.approval_id)
            approval.error_message = "AUTHORIZATION_CHANGED"
        await session.commit()
        return
    try:
        result = await run_in_threadpool(
            run_connector_action,
            credential=credential,
            action=operation.action,
            kwargs=decision.params,
            approve=True,
        )
        if operation.agent_id and operation.action in {"list_repos", "list_channels"}:
            key = "full_name" if operation.action == "list_repos" else "id"
            result = [
                row
                for row in result
                if str(row.get(key, "")).casefold() in {r.casefold() for r in decision.resources}
            ]
        encoded = json.dumps(result, default=str)
        if len(encoded.encode()) > 1048576:
            operation.status = "UNKNOWN"
            operation.error_code = "RESULT_LIMIT_EXCEEDED"
        else:
            operation.encrypted_result = secret_store.encrypt(encoded)
            operation.status = "SUCCEEDED"
    except ConnectorAuthenticationError:
        operation.status = "FAILED"
        operation.error_code = "PROVIDER_AUTHENTICATION_FAILED"
    except Exception:
        # A provider exception cannot prove that its mutation did not happen.
        operation.status = "UNKNOWN"
        operation.error_code = "PROVIDER_OUTCOME_UNKNOWN"
    operation.finished_at = datetime.now(timezone.utc)
    audit = await session.get(AuditEvent, operation.audit_id) if operation.audit_id else None
    if audit:
        audit.error_message = operation.error_code
        audit.result_json = result_summary(result) if operation.status == "SUCCEEDED" else None
    if operation.approval_id:
        approval = await session.get(ApprovalRequest, operation.approval_id)
        approval.error_message = operation.error_code
        approval.result_json = result_summary(result) if operation.status == "SUCCEEDED" else None
    # If this commit fails, the durable EXECUTING row stays recoverable as UNKNOWN, never PENDING.
    await session.commit()


def operation_response(operation, include_result=True):
    return {
        "status": "completed"
        if operation.status == "SUCCEEDED"
        else "pending_approval"
        if operation.status == "WAITING"
        else operation.status.lower(),
        "operation_id": str(operation.id),
        "approval_id": str(operation.approval_id) if operation.approval_id else None,
        "result": json.loads(secret_store.decrypt(operation.encrypted_result))
        if include_result and operation.encrypted_result
        else None,
        "error": operation.error_code,
    }
