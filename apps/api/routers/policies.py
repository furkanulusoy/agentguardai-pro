"""
Per-tenant Policy Engine -- lets a tenant admin override the system
default decision (from connectors/gmail/policy.py, connectors/github/policy.py)
for a given (connector_type, action) pair, without editing code. See
docs/PRODUCTIZATION_ROADMAP.md, Phase A, and
infrastructure/database/models/policy_rule.py's own docstring for the
"can only act within the connector's task_scope" boundary this respects.

GET returns the *effective* policy for every action the tenant's
connected connector types actually support -- the system default merged
with any tenant override -- so the dashboard never has to reimplement
the merge logic apps/api/routers/connectors/general.py itself uses at
execution time.

Every query here filters PolicyRule.agent_id IS NULL, explicitly --
Phase B (docs/PRODUCTIZATION_ROADMAP.md) added agent-scoped rows to the
same table (infrastructure/database/models/policy_rule.py), and without
that filter a lookup here could find and silently overwrite/report an
agent-specific rule instead of the tenant-wide one. Agent-scoped rules
are managed through apps/api/routers/agents.py instead, which reuses
this module's `_validate_action`/`_system_default` helpers rather than
duplicating the task_scope-boundary logic.

GET .../simulate is the Policy Simulator docs/PRODUCTIZATION_ROADMAP.md's
Phase C sketch named as a signature feature and deliberately didn't
build there ("needs Phase A's rules and Phase C's event log to both
exist first ... building the simulation itself is real, separate
work"). Both exist now. `_simulate` (shared with
apps/api/routers/agents.py's agent-scoped variant) answers a real
question an admin has *before* clicking Save on a policy change: of the
real AuditEvent history for this exact (connector_type, action) in the
last N days, how many would land differently under each of the three
possible decisions. A rule maps one (connector_type, action[, agent_id])
to exactly one decision, so "if this rule had existed" is arithmetic
over the real historical breakdown, not a re-simulation of the whole
guardrail -- deliberately scoped to what a PolicyRule actually affects,
not a broader "replay all traffic" simulator.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import DbSession, require_permission
from apps.api.services.governance_lock import lock_governance
from apps.api.services.security_events import record_security_event
from connectors.registry import CONNECTOR_CLASSES, POLICY_FACTORIES, RISK_LEVELS
from infrastructure.database.models import POLICY_DECISIONS, PolicyRule, User
from infrastructure.database.models.audit_event import AuditEvent

router = APIRouter(prefix="/tenant/policies", tags=["policies"])

RequireTenantAdmin = Annotated[User, Depends(require_permission("tenant.admin"))]

PolicyDecisionLiteral = Literal["ALLOW", "DENY", "REQUIRE_APPROVAL"]


class EffectivePolicyResponse(BaseModel):
    connector_type: str
    action: str
    risk_level: str
    system_default: PolicyDecisionLiteral
    override: PolicyDecisionLiteral | None
    effective: PolicyDecisionLiteral


class SetPolicyRequest(BaseModel):
    decision: PolicyDecisionLiteral


def _system_default(connector_type: str, action: str) -> PolicyDecisionLiteral:
    policy = POLICY_FACTORIES[connector_type]()
    return "REQUIRE_APPROVAL" if policy.requires_approval(action) else "ALLOW"


@router.get("", response_model=list[EffectivePolicyResponse])
async def list_effective_policies(
    current_user: RequireTenantAdmin, session: DbSession
) -> list[EffectivePolicyResponse]:
    result = await session.execute(
        select(PolicyRule).where(
            PolicyRule.tenant_id == current_user.tenant_id, PolicyRule.agent_id.is_(None)
        )
    )
    overrides = {(r.connector_type, r.action): r.decision for r in result.scalars().all()}

    rows: list[EffectivePolicyResponse] = []
    for connector_type in CONNECTOR_CLASSES:
        policy = POLICY_FACTORIES[connector_type]()
        for action in sorted(policy.task_scope):
            default = _system_default(connector_type, action)
            override = overrides.get((connector_type, action))
            rows.append(
                EffectivePolicyResponse(
                    connector_type=connector_type,
                    action=action,
                    risk_level=RISK_LEVELS[connector_type].get(action, "MEDIUM"),
                    system_default=default,
                    override=override,  # type: ignore[arg-type]
                    effective=override or default,  # type: ignore[arg-type]
                )
            )
    return rows


def _validate_action(connector_type: str, action: str) -> None:
    if connector_type not in POLICY_FACTORIES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Bilinmeyen connector '{connector_type}'")
    policy = POLICY_FACTORIES[connector_type]()
    if action not in policy.task_scope:
        # This is the task_scope ceiling from PolicyRule's own docstring --
        # a tenant can't set a policy on an action the connector doesn't
        # structurally support at all, regardless of decision.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"'{action}' bu connector'ın görev kapsamında değil: {sorted(policy.task_scope)}",
        )


@router.put("/{connector_type}/{action}", response_model=EffectivePolicyResponse)
async def set_policy(
    connector_type: str,
    action: str,
    body: SetPolicyRequest,
    current_user: RequireTenantAdmin,
    session: DbSession,
) -> EffectivePolicyResponse:
    await lock_governance(session, current_user.tenant_id)
    _validate_action(connector_type, action)

    result = await session.execute(
        select(PolicyRule).where(
            PolicyRule.tenant_id == current_user.tenant_id,
            PolicyRule.connector_type == connector_type,
            PolicyRule.action == action,
            PolicyRule.agent_id.is_(None),
        )
    )
    rule = result.scalar_one_or_none()
    if rule is None:
        rule = PolicyRule(
            tenant_id=current_user.tenant_id,
            connector_type=connector_type,
            action=action,
            decision=body.decision,
            agent_id=None,
            created_by_user_id=current_user.id,
        )
        session.add(rule)
    else:
        rule.decision = body.decision
        rule.created_by_user_id = current_user.id
    record_security_event(session, current_user, "set_policy", connector_type + ":" + action)
    await session.commit()

    return EffectivePolicyResponse(
        connector_type=connector_type,
        action=action,
        risk_level=RISK_LEVELS[connector_type].get(action, "MEDIUM"),
        system_default=_system_default(connector_type, action),
        override=body.decision,  # type: ignore[arg-type]
        effective=body.decision,  # type: ignore[arg-type]
    )


@router.delete("/{connector_type}/{action}", status_code=status.HTTP_204_NO_CONTENT)
async def clear_policy(
    connector_type: str, action: str, current_user: RequireTenantAdmin, session: DbSession
) -> None:
    await lock_governance(session, current_user.tenant_id)
    result = await session.execute(
        select(PolicyRule).where(
            PolicyRule.tenant_id == current_user.tenant_id,
            PolicyRule.connector_type == connector_type,
            PolicyRule.action == action,
            PolicyRule.agent_id.is_(None),
        )
    )
    rule = result.scalar_one_or_none()
    if rule is not None:
        await session.delete(rule)
        record_security_event(session, current_user, "clear_policy", connector_type + ":" + action)
        await session.commit()


class PolicySimulationResponse(BaseModel):
    connector_type: str
    action: str
    window_days: int
    total_events: int
    # What actually happened, in the real event log, for these events.
    historical_breakdown: dict[str, int]
    # For each of the three possible decisions: how many of total_events
    # would have gotten a DIFFERENT outcome than they actually got, had
    # that decision been the rule in effect the whole time. The decision
    # matching today's `historical_breakdown` majority naturally comes
    # back near 0 -- that's a correct answer, not a bug.
    would_change_if: dict[str, int]


async def _simulate(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    connector_type: str,
    action: str,
    window_days: int,
    agent_id: uuid.UUID | None,
) -> PolicySimulationResponse:
    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    query = (
        select(AuditEvent.decision, func.count())
        .where(
            AuditEvent.tenant_id == tenant_id,
            AuditEvent.credential.has(connector_type=connector_type),
            AuditEvent.action == action,
            AuditEvent.created_at >= since,
        )
        .group_by(AuditEvent.decision)
    )
    if agent_id is not None:
        query = query.where(AuditEvent.agent_id == agent_id)

    result = await session.execute(query)
    counts = {decision: count for decision, count in result.all()}
    total = sum(counts.values())
    historical_breakdown = {d: counts.get(d, 0) for d in POLICY_DECISIONS}
    would_change_if = {d: total - counts.get(d, 0) for d in POLICY_DECISIONS}

    return PolicySimulationResponse(
        connector_type=connector_type,
        action=action,
        window_days=window_days,
        total_events=total,
        historical_breakdown=historical_breakdown,
        would_change_if=would_change_if,
    )


@router.get("/{connector_type}/{action}/simulate", response_model=PolicySimulationResponse)
async def simulate_policy(
    connector_type: str,
    action: str,
    current_user: RequireTenantAdmin,
    session: DbSession,
    days: Annotated[int, Query(ge=1, le=90)] = 7,
) -> PolicySimulationResponse:
    _validate_action(connector_type, action)
    return await _simulate(
        session, current_user.tenant_id, connector_type, action, days, agent_id=None
    )
