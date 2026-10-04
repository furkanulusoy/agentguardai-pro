"""
Agent identity -- create/list/revoke an Agent
(infrastructure/database/models/agent.py), and view/set the per-agent
policy overrides layered on top of the tenant-wide ones from
apps/api/routers/policies.py. See docs/PRODUCTIZATION_ROADMAP.md,
Phase B.

POST /tenant/agents returns the raw API key exactly once -- same
one-time-reveal-then-hash pattern as every other credential in this
project (invites, password resets): only its sha256 hash is ever
stored (Agent.api_key_hash). The admin copies it into wherever the
agent process reads its config from (apps/mcp_server's
AGENTGUARD_AGENT_KEY env var, see that module's own docstring) --
there is no way to retrieve it again after this response, only to
revoke it and issue a new one.

GET /tenant/agents/{id}/policies is the "what can this agent do"
signature feature named in docs/PRODUCTIZATION_ROADMAP.md's Phase B --
mostly a read-only view once Phase A's policy resolution and this
phase's agent-scoped PolicyRule rows both exist, so it's genuinely
cheap here rather than a separate build.

GET/PUT/DELETE /tenant/agents/{id}/connectors/* close the other half of
Phase B's original sketch ("its own scoped credential grants") --
which connectors an agent may act on at all, not just which actions.
See infrastructure/database/models/agent_credential_grant.py's own
docstring for the deny-by-default reasoning.

GET .../policies/{connector_type}/{action}/simulate reuses
apps/api/routers/policies.py's `_simulate` for the agent-scoped case --
same Policy Simulator, just filtered to this one agent's own history.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from apps.api.dependencies import AGENT_KEY_PREFIX, DbSession, require_permission
from apps.api.routers.policies import (
    PolicyDecisionLiteral,
    PolicySimulationResponse,
    SetPolicyRequest,
    _simulate,
    _system_default,
    _validate_action,
)
from apps.api.services.governance import merge_policy
from apps.api.services.governance_lock import lock_governance
from apps.api.services.security_events import record_security_event
from connectors.registry import CONNECTOR_CLASSES, POLICY_FACTORIES, RISK_LEVELS
from infrastructure.database.models import (
    Agent,
    AgentCredentialGrant,
    AgentPermissionGrant,
    Credential,
    Permission,
    PolicyRule,
    Role,
    User,
)

router = APIRouter(prefix="/tenant/agents", tags=["agents"])

RequireTenantAdmin = Annotated[User, Depends(require_permission("tenant.admin"))]


class AgentResponse(BaseModel):
    id: uuid.UUID
    name: str
    owner_email: str | None
    # Deliberately surfaced, not just tracked internally: an owner
    # shown on the dashboard should be able to see who actually created
    # an agent "owned by" them, not just take the owner label on faith
    # -- the whole point of the OWNER-exclusion check above is defeated
    # if this stays invisible.
    created_by_email: str | None
    created_at: datetime
    is_revoked: bool
    # Only ever populated on the response to POST itself -- see this
    # module's own docstring.
    api_key: str | None = None


class CreateAgentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    # Defaults to the creating admin if omitted -- see this module's
    # own docstring on why an Agent has an owner at all.
    owner_user_id: uuid.UUID | None = None


async def _load_agent(session: DbSession, tenant_id: uuid.UUID, agent_id: uuid.UUID) -> Agent:
    result = await session.execute(
        select(Agent)
        .options(selectinload(Agent.owner), selectinload(Agent.created_by))
        .where(Agent.id == agent_id, Agent.tenant_id == tenant_id)
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        # Same 404-regardless-of-reason discipline as every other
        # tenant-scoped lookup in this project (e.g.
        # apps/api/routers/tenant.py's revoke_invite).
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent bulunamadı")
    return agent


def _to_response(agent: Agent, *, api_key: str | None = None) -> AgentResponse:
    return AgentResponse(
        id=agent.id,
        name=agent.name,
        owner_email=agent.owner.email if agent.owner else None,
        created_by_email=agent.created_by.email if agent.created_by else None,
        created_at=agent.created_at,
        is_revoked=agent.revoked_at is not None,
        api_key=api_key,
    )


@router.post("", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
async def create_agent(
    body: CreateAgentRequest, current_user: RequireTenantAdmin, session: DbSession
) -> AgentResponse:
    await lock_governance(session, current_user.tenant_id)
    owner_id = body.owner_user_id or current_user.id
    if owner_id != current_user.id:
        owner_check = await session.execute(
            select(User)
            .options(selectinload(User.roles))
            .where(User.id == owner_id, User.tenant_id == current_user.tenant_id)
        )
        target_owner = owner_check.scalar_one_or_none()
        if target_owner is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "owner_user_id bu tenant'a ait bir üye değil"
            )
        # Same reasoning as INVITABLE_ROLES (apps/api/routers/tenant.py)
        # excluding OWNER from invites, and reset_member_password's
        # target_is_owner check: an Agent's effective permissions are
        # its owner's in full (infrastructure/database/models/agent.py's
        # own docstring), so a mere ADMIN minting an agent "owned by"
        # the OWNER would mint itself a credential that acts -- and gets
        # attributed in every ApprovalRequest/AuditEvent -- as the
        # OWNER. A privilege-escalation and audit-integrity break
        # either way, not just an inconvenience.
        target_is_owner = any(r.name == "OWNER" for r in target_owner.roles)
        requester_is_owner = any(r.name == "OWNER" for r in current_user.roles)
        if target_is_owner and not requester_is_owner:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Bir OWNER adına yalnızca başka bir OWNER agent oluşturabilir",
            )

    raw_key = AGENT_KEY_PREFIX + secrets.token_urlsafe(32)
    agent = Agent(
        tenant_id=current_user.tenant_id,
        name=body.name,
        api_key_hash=hashlib.sha256(raw_key.encode("utf-8")).hexdigest(),
        owner_user_id=owner_id,
        created_by_user_id=current_user.id,
    )
    # created_by is always the caller themselves -- setting it directly
    # avoids a lazy load SQLAlchemy's async ORM doesn't support outside
    # awaitable_attrs (same trick used throughout this codebase, e.g.
    # apps/api/routers/connectors/general.py's approval.requested_by).
    agent.created_by = current_user
    session.add(agent)
    await session.flush()
    # Explicit, minimal initial grants; removing the last grant always denies.
    permissions = (
        (
            await session.execute(
                select(Permission).where(
                    Permission.code.in_(["agent.execute", "connector.read", "approval.read"])
                )
            )
        )
        .scalars()
        .all()
    )
    for permission in permissions:
        session.add(
            AgentPermissionGrant(
                agent_id=agent.id, permission_id=permission.id, created_by_user_id=current_user.id
            )
        )
    record_security_event(session, current_user, "create_agent", agent.id)
    await session.commit()
    await session.refresh(agent, attribute_names=["owner"])

    return _to_response(agent, api_key=raw_key)


@router.get("", response_model=list[AgentResponse])
async def list_agents(current_user: RequireTenantAdmin, session: DbSession) -> list[AgentResponse]:
    result = await session.execute(
        select(Agent)
        .options(selectinload(Agent.owner), selectinload(Agent.created_by))
        .where(Agent.tenant_id == current_user.tenant_id)
        .order_by(Agent.created_at.desc())
    )
    return [_to_response(a) for a in result.scalars().all()]


@router.post("/{agent_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_agent(
    agent_id: uuid.UUID, current_user: RequireTenantAdmin, session: DbSession
) -> None:
    await lock_governance(session, current_user.tenant_id)
    agent = await _load_agent(session, current_user.tenant_id, agent_id)
    if agent.revoked_at is None:
        agent.revoked_at = datetime.now(timezone.utc)
        record_security_event(session, current_user, "revoke_agent", agent_id)
        await session.commit()


class AgentEffectivePolicyResponse(BaseModel):
    connector_type: str
    action: str
    risk_level: str
    system_default: PolicyDecisionLiteral
    tenant_override: PolicyDecisionLiteral | None
    agent_override: PolicyDecisionLiteral | None
    effective: PolicyDecisionLiteral


@router.get("/{agent_id}/policies", response_model=list[AgentEffectivePolicyResponse])
async def list_agent_policies(
    agent_id: uuid.UUID, current_user: RequireTenantAdmin, session: DbSession
) -> list[AgentEffectivePolicyResponse]:
    await _load_agent(session, current_user.tenant_id, agent_id)

    result = await session.execute(
        select(PolicyRule).where(
            PolicyRule.tenant_id == current_user.tenant_id,
            (PolicyRule.agent_id.is_(None)) | (PolicyRule.agent_id == agent_id),
        )
    )
    tenant_overrides: dict[tuple[str, str], str] = {}
    agent_overrides: dict[tuple[str, str], str] = {}
    for rule in result.scalars().all():
        key = (rule.connector_type, rule.action)
        if rule.agent_id is None:
            tenant_overrides[key] = rule.decision
        else:
            agent_overrides[key] = rule.decision

    rows: list[AgentEffectivePolicyResponse] = []
    for connector_type in CONNECTOR_CLASSES:
        policy = POLICY_FACTORIES[connector_type]()
        for action in sorted(policy.task_scope):
            key = (connector_type, action)
            default = _system_default(connector_type, action)
            tenant_override = tenant_overrides.get(key)
            agent_override = agent_overrides.get(key)
            effective = merge_policy(default, tenant_override, agent_override)[0]
            rows.append(
                AgentEffectivePolicyResponse(
                    connector_type=connector_type,
                    action=action,
                    risk_level=RISK_LEVELS[connector_type].get(action, "MEDIUM"),
                    system_default=default,
                    tenant_override=tenant_override,  # type: ignore[arg-type]
                    agent_override=agent_override,  # type: ignore[arg-type]
                    effective=effective,  # type: ignore[arg-type]
                )
            )
    return rows


@router.put(
    "/{agent_id}/policies/{connector_type}/{action}",
    response_model=AgentEffectivePolicyResponse,
)
async def set_agent_policy(
    agent_id: uuid.UUID,
    connector_type: str,
    action: str,
    body: SetPolicyRequest,
    current_user: RequireTenantAdmin,
    session: DbSession,
) -> AgentEffectivePolicyResponse:
    await lock_governance(session, current_user.tenant_id)
    await _load_agent(session, current_user.tenant_id, agent_id)
    _validate_action(connector_type, action)

    result = await session.execute(
        select(PolicyRule).where(
            PolicyRule.tenant_id == current_user.tenant_id,
            PolicyRule.connector_type == connector_type,
            PolicyRule.action == action,
            PolicyRule.agent_id == agent_id,
        )
    )
    rule = result.scalar_one_or_none()
    if rule is None:
        rule = PolicyRule(
            tenant_id=current_user.tenant_id,
            connector_type=connector_type,
            action=action,
            decision=body.decision,
            agent_id=agent_id,
            created_by_user_id=current_user.id,
        )
        session.add(rule)
    else:
        rule.decision = body.decision
        rule.created_by_user_id = current_user.id
    record_security_event(session, current_user, "set_agent_policy", agent_id)
    await session.commit()

    tenant_result = await session.execute(
        select(PolicyRule.decision).where(
            PolicyRule.tenant_id == current_user.tenant_id,
            PolicyRule.connector_type == connector_type,
            PolicyRule.action == action,
            PolicyRule.agent_id.is_(None),
        )
    )
    tenant_override = tenant_result.scalar_one_or_none()

    return AgentEffectivePolicyResponse(
        connector_type=connector_type,
        action=action,
        risk_level=RISK_LEVELS[connector_type].get(action, "MEDIUM"),
        system_default=_system_default(connector_type, action),
        tenant_override=tenant_override,  # type: ignore[arg-type]
        agent_override=body.decision,  # type: ignore[arg-type]
        effective=merge_policy(
            _system_default(connector_type, action), tenant_override, body.decision
        )[0],  # type: ignore[arg-type]
    )


@router.delete(
    "/{agent_id}/policies/{connector_type}/{action}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def clear_agent_policy(
    agent_id: uuid.UUID,
    connector_type: str,
    action: str,
    current_user: RequireTenantAdmin,
    session: DbSession,
) -> None:
    await lock_governance(session, current_user.tenant_id)
    await _load_agent(session, current_user.tenant_id, agent_id)
    result = await session.execute(
        select(PolicyRule).where(
            PolicyRule.tenant_id == current_user.tenant_id,
            PolicyRule.connector_type == connector_type,
            PolicyRule.action == action,
            PolicyRule.agent_id == agent_id,
        )
    )
    rule = result.scalar_one_or_none()
    if rule is not None:
        await session.delete(rule)
        record_security_event(session, current_user, "clear_agent_policy", agent_id)
        await session.commit()


class AgentConnectorGrantResponse(BaseModel):
    credential_id: uuid.UUID
    connector_type: str
    label: str
    is_granted: bool
    resources: list[str] = []


class GrantResourcesRequest(BaseModel):
    resources: list[str] = Field(default_factory=list, max_length=100)
    model_config = {"extra": "forbid"}


@router.get("/{agent_id}/connectors", response_model=list[AgentConnectorGrantResponse])
async def list_agent_connector_grants(
    agent_id: uuid.UUID, current_user: RequireTenantAdmin, session: DbSession
) -> list[AgentConnectorGrantResponse]:
    await _load_agent(session, current_user.tenant_id, agent_id)

    creds_result = await session.execute(
        select(Credential)
        .where(Credential.tenant_id == current_user.tenant_id, Credential.revoked_at.is_(None))
        .order_by(Credential.created_at.desc())
    )
    credentials = creds_result.scalars().all()

    grants_result = await session.execute(
        select(AgentCredentialGrant).where(AgentCredentialGrant.agent_id == agent_id)
    )
    grant_map = {g.credential_id: g for g in grants_result.scalars()}
    granted_ids = set(grant_map)

    return [
        AgentConnectorGrantResponse(
            credential_id=c.id,
            connector_type=c.connector_type,
            label=c.label,
            is_granted=c.id in granted_ids,
            resources=json.loads(grant_map[c.id].resource_scope) if c.id in grant_map else [],
        )
        for c in credentials
    ]


@router.put(
    "/{agent_id}/connectors/{credential_id}",
    response_model=AgentConnectorGrantResponse,
)
async def grant_agent_connector(
    agent_id: uuid.UUID,
    credential_id: uuid.UUID,
    body: GrantResourcesRequest,
    current_user: RequireTenantAdmin,
    session: DbSession,
) -> AgentConnectorGrantResponse:
    await lock_governance(session, current_user.tenant_id)
    await _load_agent(session, current_user.tenant_id, agent_id)
    cred_result = await session.execute(
        select(Credential).where(
            Credential.id == credential_id, Credential.tenant_id == current_user.tenant_id
        )
    )
    credential = cred_result.scalar_one_or_none()
    if credential is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Connector bulunamadı")

    existing = await session.execute(
        select(AgentCredentialGrant).where(
            AgentCredentialGrant.agent_id == agent_id,
            AgentCredentialGrant.credential_id == credential_id,
        )
    )
    grant = existing.scalar_one_or_none()
    resources = sorted(set(v.strip() for v in body.resources if v.strip()))
    if any(len(v) > 200 or "*" in v for v in resources):
        raise HTTPException(422, "Explicit resource identifiers required; wildcards are forbidden")
    if grant is not None:
        grant.resource_scope = json.dumps(resources)
        record_security_event(session, current_user, "grant_agent_connector", agent_id)
        await session.commit()
    else:
        session.add(
            AgentCredentialGrant(
                agent_id=agent_id,
                credential_id=credential_id,
                resource_scope=json.dumps(resources),
                created_by_user_id=current_user.id,
            )
        )
        record_security_event(session, current_user, "grant_agent_connector", agent_id)
        await session.commit()

    return AgentConnectorGrantResponse(
        credential_id=credential.id,
        connector_type=credential.connector_type,
        label=credential.label,
        is_granted=True,
        resources=resources,
    )


@router.delete(
    "/{agent_id}/connectors/{credential_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_agent_connector(
    agent_id: uuid.UUID,
    credential_id: uuid.UUID,
    current_user: RequireTenantAdmin,
    session: DbSession,
) -> None:
    await lock_governance(session, current_user.tenant_id)
    await _load_agent(session, current_user.tenant_id, agent_id)
    result = await session.execute(
        select(AgentCredentialGrant).where(
            AgentCredentialGrant.agent_id == agent_id,
            AgentCredentialGrant.credential_id == credential_id,
        )
    )
    grant = result.scalar_one_or_none()
    if grant is not None:
        await session.delete(grant)
        record_security_event(session, current_user, "revoke_agent_connector", agent_id)
        await session.commit()


class AgentPermissionGrantResponse(BaseModel):
    code: str
    description: str
    is_granted: bool


@router.get("/{agent_id}/permissions", response_model=list[AgentPermissionGrantResponse])
async def list_agent_permission_grants(
    agent_id: uuid.UUID, current_user: RequireTenantAdmin, session: DbSession
) -> list[AgentPermissionGrantResponse]:
    """Only lists permissions the agent's OWNER currently holds --
    granting one the owner doesn't have would never take effect anyway
    (apps/api/dependencies.py's require_actor_permission intersects
    with the owner's live roles on every request), so showing it here
    would be misleading, not just unused."""
    agent = await _load_agent(session, current_user.tenant_id, agent_id)
    if agent.owner is None:
        return []

    owner_result = await session.execute(
        select(User)
        .options(selectinload(User.roles).selectinload(Role.permissions))
        .where(User.id == agent.owner_user_id)
    )
    owner = owner_result.scalar_one_or_none()
    if owner is None:
        return []
    owner_permissions = {p.code: p for role in owner.roles for p in role.permissions}

    grants_result = await session.execute(
        select(AgentPermissionGrant.permission_id).where(AgentPermissionGrant.agent_id == agent_id)
    )
    granted_ids = {row[0] for row in grants_result.all()}

    return [
        AgentPermissionGrantResponse(
            code=perm.code,
            description=perm.description,
            is_granted=perm.id in granted_ids,
        )
        for perm in sorted(owner_permissions.values(), key=lambda p: p.code)
    ]


@router.put(
    "/{agent_id}/permissions/{permission_code}",
    response_model=AgentPermissionGrantResponse,
)
async def grant_agent_permission(
    agent_id: uuid.UUID,
    permission_code: str,
    current_user: RequireTenantAdmin,
    session: DbSession,
) -> AgentPermissionGrantResponse:
    await lock_governance(session, current_user.tenant_id)
    agent = await _load_agent(session, current_user.tenant_id, agent_id)

    perm_result = await session.execute(
        select(Permission).where(Permission.code == permission_code)
    )
    permission = perm_result.scalar_one_or_none()
    if permission is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bilinmeyen permission code")

    if agent.owner_user_id is not None:
        owner_result = await session.execute(
            select(User)
            .options(selectinload(User.roles).selectinload(Role.permissions))
            .where(User.id == agent.owner_user_id)
        )
        owner = owner_result.scalar_one_or_none()
        owner_codes = {p.code for role in owner.roles for p in role.permissions} if owner else set()
    else:
        owner_codes = set()
    if permission_code not in owner_codes:
        # Grant would be inert anyway (require_actor_permission always
        # intersects with the owner's own held permissions) -- refusing
        # it outright here is a clearer signal than silently accepting
        # a grant that can never take effect.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Agent'ın sahibi '{permission_code}' iznine sahip değil -- bu izin verilemez.",
        )

    existing = await session.execute(
        select(AgentPermissionGrant).where(
            AgentPermissionGrant.agent_id == agent_id,
            AgentPermissionGrant.permission_id == permission.id,
        )
    )
    if existing.scalar_one_or_none() is None:
        session.add(
            AgentPermissionGrant(
                agent_id=agent_id,
                permission_id=permission.id,
                created_by_user_id=current_user.id,
            )
        )
        record_security_event(session, current_user, "grant_agent_permission", agent_id)
        await session.commit()

    return AgentPermissionGrantResponse(
        code=permission.code, description=permission.description, is_granted=True
    )


@router.delete(
    "/{agent_id}/permissions/{permission_code}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_agent_permission(
    agent_id: uuid.UUID,
    permission_code: str,
    current_user: RequireTenantAdmin,
    session: DbSession,
) -> None:
    await lock_governance(session, current_user.tenant_id)
    await _load_agent(session, current_user.tenant_id, agent_id)
    result = await session.execute(
        select(AgentPermissionGrant)
        .join(Permission, Permission.id == AgentPermissionGrant.permission_id)
        .where(AgentPermissionGrant.agent_id == agent_id, Permission.code == permission_code)
    )
    grant = result.scalar_one_or_none()
    if grant is not None:
        await session.delete(grant)
        record_security_event(session, current_user, "revoke_agent_permission", agent_id)
        await session.commit()


@router.get(
    "/{agent_id}/policies/{connector_type}/{action}/simulate",
    response_model=PolicySimulationResponse,
)
async def simulate_agent_policy(
    agent_id: uuid.UUID,
    connector_type: str,
    action: str,
    current_user: RequireTenantAdmin,
    session: DbSession,
    days: Annotated[int, Query(ge=1, le=90)] = 7,
) -> PolicySimulationResponse:
    await _load_agent(session, current_user.tenant_id, agent_id)
    _validate_action(connector_type, action)
    return await _simulate(
        session, current_user.tenant_id, connector_type, action, days, agent_id=agent_id
    )
