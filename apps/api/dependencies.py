"""
Shared FastAPI dependencies. get_current_user is the one every human-
facing protected route depends on -- it eager-loads `roles` AND each
role's `permissions` (nested selectinload) so handlers, and
require_permission below, can read them safely: SQLAlchemy's async ORM
does not support implicit lazy-loading of relationships outside of
`awaitable_attrs`, and accessing an unloaded relationship here would
raise MissingGreenlet instead of quietly doing an extra query.

require_permission(code) enforces authorization deterministically, in
this dependency layer -- never inferred by an LLM/agent and never left
to the frontend to hide-but-not-actually-block. This is the same
enforcement-point principle agentguard/guardrail.py already applies to
tool calls; RBAC is that same idea one layer up, for the platform's own
HTTP API.

Actor/get_current_actor/require_actor_permission (Phase B,
docs/PRODUCTIZATION_ROADMAP.md) is a second, narrower entry point used
only by apps/api/routers/connectors/general.py's execute_connector_action
-- the one route an Agent (infrastructure/database/models/agent.py),
not just a human, needs to call directly. It is deliberately NOT a
replacement for get_current_user/require_permission: every other route
in this codebase keeps depending on those, untouched, so this addition
carries zero risk to anything that doesn't explicitly opt into it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from infrastructure.auth.jwt import decode_access_token
from infrastructure.database.models import (
    Agent,
    AgentPermissionGrant,
    Permission,
    Role,
    Tenant,
    User,
)
from infrastructure.database.session import get_session

_bearer_scheme = HTTPBearer(auto_error=False)

BearerCredentials = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)]
DbSession = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(
    credentials: BearerCredentials,
    session: DbSession,
) -> User:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")

    claims = decode_access_token(credentials.credentials)
    if claims is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")

    result = await session.execute(
        select(User)
        .options(selectinload(User.roles).selectinload(Role.permissions))
        .where(User.id == claims.user_id)
    )
    user = result.scalar_one_or_none()
    if (
        user is None
        or not user.is_active
        or user.auth_version != claims.auth_version
        or user.tenant_id != claims.tenant_id
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")
    tenant = await session.get(Tenant, user.tenant_id)
    if tenant is None or not tenant.is_active:
        raise HTTPException(401, "Workspace inactive")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_permission(permission_code: str):
    """Returns a dependency that 403s unless current_user holds a role
    granting `permission_code`. Deny-by-default: a user with zero roles,
    or roles with no matching permission, is rejected -- there is no
    implicit "everyone can" fallback."""

    async def _check(current_user: CurrentUser) -> User:
        held = {p.code for role in current_user.roles for p in role.permissions}
        if permission_code not in held:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Missing required permission: {permission_code}",
            )
        return current_user

    return _check


# Distinguishes an agent's own API key from a human's JWT on sight, the
# same way e.g. GitHub's ghp_/Stripe's sk_ prefixes do -- avoids a
# fragile "try to decode as a JWT, catch, fall back" cascade.
AGENT_KEY_PREFIX = "agk_"


@dataclass
class Actor:
    """Whoever/whatever is calling: `user` is whose *permissions* apply
    (an Agent has none of its own -- see infrastructure/database/models/
    agent.py's docstring -- so this is always its owner), `agent` is set
    only when an Agent's own API key authenticated the call, never a
    human's JWT."""

    user: User
    agent: Agent | None


async def get_current_actor(credentials: BearerCredentials, session: DbSession) -> Actor:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")

    token = credentials.credentials
    if not token.startswith(AGENT_KEY_PREFIX):
        return Actor(user=await get_current_user(credentials, session), agent=None)

    key_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    result = await session.execute(
        select(Agent).where(Agent.api_key_hash == key_hash, Agent.revoked_at.is_(None))
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or revoked agent key")

    owner_result = await session.execute(
        select(User)
        .options(selectinload(User.roles).selectinload(Role.permissions))
        .where(User.id == agent.owner_user_id)
    )
    owner = owner_result.scalar_one_or_none()
    if owner is None or not owner.is_active:
        # The owner account was deleted/deactivated after this agent was
        # created -- correct to refuse rather than silently run with no
        # permissions at all (an empty `roles` would just look like a
        # confusing 403 further down, not an honest 401 about the real
        # cause).
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Agent's owner account is unavailable")

    tenant = await session.get(Tenant, agent.tenant_id)
    if owner.tenant_id != agent.tenant_id or tenant is None or not tenant.is_active:
        raise HTTPException(401, "Workspace inactive")
    return Actor(user=owner, agent=agent)


CurrentActor = Annotated[Actor, Depends(get_current_actor)]


def require_actor_permission(permission_code: str):
    """Same deny-by-default rule as require_permission, evaluated against
    the Actor's effective permission set -- which is its owner's in
    full UNLESS the Agent itself has at least one
    infrastructure/database/models/agent_permission_grant.py row, in
    which case it narrows to the intersection of the owner's held
    permissions and the agent's own granted set (see that model's own
    docstring for why this defaults to "inherit," not "deny," unlike
    AgentCredentialGrant). Re-evaluated on every request against the
    owner's CURRENT roles, not cached at grant time -- an agent can
    never end up holding a permission its owner doesn't currently have,
    even if the owner's roles were narrowed after the grant was made.
    A human caller (actor.agent is None) is unaffected; this only ever
    narrows an Agent's own effective set."""

    async def _check(actor: CurrentActor, session: DbSession) -> Actor:
        owner_held = {p.code for role in actor.user.roles for p in role.permissions}
        held = owner_held
        if actor.agent is not None:
            grant_result = await session.execute(
                select(Permission.code)
                .join(AgentPermissionGrant, AgentPermissionGrant.permission_id == Permission.id)
                .where(AgentPermissionGrant.agent_id == actor.agent.id)
            )
            agent_scoped = {row[0] for row in grant_result.all()}
            held = owner_held & agent_scoped
        if permission_code not in held:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Missing required permission: {permission_code}",
            )
        return actor

    return _check
