"""
An Agent's own, narrower-than-its-owner's RBAC permission set --
closes the gap infrastructure/database/models/agent.py's own docstring
and docs/PRODUCTIZATION_ROADMAP.md have both named since Phase B: "its
effective permissions are its owner's... a true independently-scoped,
narrower grant per agent is real future work."

Deliberately NOT deny-by-default the way
infrastructure/database/models/agent_credential_grant.py's own grants
are. An Agent with zero AgentPermissionGrant rows keeps inheriting its
owner's full permission set -- unchanged from Phase B's original shape,
so every Agent created before this model existed keeps working exactly
as it did. That's a different tradeoff than AgentCredentialGrant on
purpose: "which connectors can this agent touch" is meaningful to leave
empty (an Agent with zero connectors just can't do anything useful yet,
which is a safe, inert default). "Which RBAC permissions does this
agent have at all" is not the same kind of question -- agent.execute/
connector.read/approval.read are what an Agent *is*, not an optional
extra; defaulting that to empty would break every existing Agent's
basic ability to function the moment this model shipped, which is a
real regression, not a tightening.

What this DOES add: once a tenant admin grants at least one
AgentPermissionGrant to an agent, that agent's effective permissions
narrow to the INTERSECTION of its owner's held permissions and its own
granted set -- see apps/api/dependencies.py's require_actor_permission
for where this is evaluated. The intersection (not the grant set alone)
matters: an agent can never end up with a permission its owner doesn't
currently hold, even if a grant was made while the owner held it and
the owner's roles were narrowed afterward -- this is re-evaluated on
every request, not cached at grant time.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infrastructure.database.models.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from infrastructure.database.models.agent import Agent
    from infrastructure.database.models.rbac import Permission
    from infrastructure.database.models.user import User


class AgentPermissionGrant(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "agent_permission_grants"
    __table_args__ = (
        UniqueConstraint("agent_id", "permission_id", name="uq_agent_permission_grant"),
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    agent: Mapped[Agent] = relationship("Agent")
    permission: Mapped[Permission] = relationship("Permission")
    created_by: Mapped[User | None] = relationship("User")
