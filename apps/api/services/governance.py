"""Single platform authorization and policy decision point, reused at submission and execution."""

import json
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from apps.api.services.governance_lock import lock_governance
from apps.api.services.privacy import fingerprint
from connectors.registry import POLICY_FACTORIES, RISK_LEVELS
from connectors.schemas import validate_params
from infrastructure.database.models import (
    Agent,
    AgentCredentialGrant,
    AgentPermissionGrant,
    Credential,
    Permission,
    PolicyRule,
    Role,
    Tenant,
    User,
)


@dataclass(frozen=True)
class Decision:
    decision: str
    source: str
    risk: str
    policy_hash: str
    params: dict
    resources: tuple[str, ...] = ()


def merge_policy(default: str, tenant: str | None, agent: str | None) -> tuple[str, str]:
    # Tenant DENY is an organizational ceiling. Agent rules can never reopen it.
    if tenant == "DENY":
        return "DENY", "TENANT_POLICY"
    choice, source = (
        (agent, "AGENT_POLICY")
        if agent is not None
        else (tenant, "TENANT_POLICY")
        if tenant is not None
        else (default, "SYSTEM_DEFAULT")
    )
    return (
        (choice, source)
        if choice in {"ALLOW", "DENY", "REQUIRE_APPROVAL"}
        else ("DENY", "INVALID_POLICY")
    )


async def authorize(session, *, user_id, agent_id, credential, action, params) -> Decision:
    if credential is None:
        return Decision("DENY", "CREDENTIAL_REVOKED", "HIGH", fingerprint("CREDENTIAL_REVOKED"), {})
    await lock_governance(session, credential.tenant_id)
    credential = await session.get(Credential, credential.id, populate_existing=True)
    policy = POLICY_FACTORIES.get(credential.connector_type)
    risk = RISK_LEVELS.get(credential.connector_type, {}).get(action, "HIGH")

    def denied(source):
        return Decision("DENY", source, risk, fingerprint(source), {})

    tenant = await session.get(Tenant, credential.tenant_id, populate_existing=True)
    user = (
        await session.execute(
            select(User)
            .options(selectinload(User.roles).selectinload(Role.permissions))
            .where(User.id == user_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if (
        user is None
        or not user.is_active
        or tenant is None
        or not tenant.is_active
        or user.tenant_id != credential.tenant_id
    ):
        return denied("IDENTITY_INACTIVE")
    if credential.revoked_at is not None:
        return denied("CREDENTIAL_REVOKED")
    held = {p.code for role in user.roles for p in role.permissions}
    resources: tuple[str, ...] = ()
    if agent_id is not None:
        agent = await session.get(Agent, agent_id, populate_existing=True)
        if (
            agent is None
            or agent.revoked_at
            or agent.owner_user_id != user.id
            or agent.tenant_id != tenant.id
        ):
            return denied("AGENT_REVOKED")
        granted = set(
            (
                await session.execute(
                    select(Permission.code)
                    .join(AgentPermissionGrant, AgentPermissionGrant.permission_id == Permission.id)
                    .where(AgentPermissionGrant.agent_id == agent.id)
                )
            ).scalars()
        )
        held &= granted
        grant = (
            await session.execute(
                select(AgentCredentialGrant)
                .where(
                    AgentCredentialGrant.agent_id == agent.id,
                    AgentCredentialGrant.credential_id == credential.id,
                )
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if grant is None:
            return denied("AGENT_NOT_GRANTED")
        resources = tuple(json.loads(grant.resource_scope))
    if "agent.execute" not in held:
        return denied("EXECUTION_PERMISSION_REVOKED")
    if policy is None or not policy().is_permitted(action):
        return denied("TASK_SCOPE")
    parsed = validate_params(credential.connector_type, action, params)
    # Credential itself scopes Gmail to one mailbox. GitHub/Slack grants scope named resources.
    resource = (
        parsed.get("repo")
        if credential.connector_type == "github"
        else parsed.get("channel") or parsed.get("name")
        if credential.connector_type == "slack"
        else None
    )
    if (
        agent_id
        and resource is not None
        and str(resource).casefold() not in {r.casefold() for r in resources}
    ):
        return denied("RESOURCE_NOT_GRANTED")
    rows = (
        (
            await session.execute(
                select(PolicyRule)
                .where(
                    PolicyRule.tenant_id == tenant.id,
                    PolicyRule.connector_type == credential.connector_type,
                    PolicyRule.action == action,
                    or_(PolicyRule.agent_id.is_(None), PolicyRule.agent_id == agent_id)
                    if agent_id
                    else PolicyRule.agent_id.is_(None),
                )
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    tenant_rule = next((r for r in rows if r.agent_id is None), None)
    agent_rule = next((r for r in rows if r.agent_id is not None), None)
    decision, source = merge_policy(
        "REQUIRE_APPROVAL" if policy().requires_approval(action) else "ALLOW",
        tenant_rule.decision if tenant_rule else None,
        agent_rule.decision if agent_rule else None,
    )
    signature = fingerprint(
        {
            "decision": decision,
            "source": source,
            "rules": [
                (str(r.id), r.decision, str(r.updated_at))
                for r in sorted(rows, key=lambda r: str(r.id))
            ],
            "resources": resources,
        }
    )
    return Decision(decision, source, risk, signature, parsed, resources)
