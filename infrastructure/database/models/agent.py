"""
A first-class identity for an AI agent connecting to this platform,
distinct from the human accountable for it -- see
docs/PRODUCTIZATION_ROADMAP.md, Phase B. Before this, apps/mcp_server
authenticated as a human's own JWT (its own module docstring already
named this a known gap), so an executed action could only ever be
attributed to whichever person's login the agent process happened to
use -- "who did this" could only answer with a human's name, never
which agent.

An Agent authenticates with its own long-lived API key (api_key_hash,
sha256 -- same one-time-reveal-then-hash pattern as every other
credential in this project: invitations, password resets). It does
NOT have its own RBAC role set; its effective permissions default to
its owner's (owner_user_id) in full, narrowed only if a tenant admin
explicitly grants it its own, smaller permission set -- see
infrastructure/database/models/agent_permission_grant.py for that
mechanism and why it defaults to "inherit," not "deny," unlike
AgentCredentialGrant below. What's also independently scoped: which
connectors it may touch at all (AgentCredentialGrant) and policy:
PolicyRule.agent_id (nullable) lets a tenant admin tighten or loosen
one specific agent's decision for an action without touching the
tenant-wide default every other agent still uses -- see
apps/api/routers/agents.py.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infrastructure.database.models.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from infrastructure.database.models.user import User


class Agent(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "agents"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    # The human accountable for this agent's actions -- effective
    # permissions are read from here (see module docstring), not a
    # separate agent-level role set. SET NULL, not CASCADE: deleting the
    # owner account must not silently delete the agent's own identity or
    # its action history's attribution.
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    owner: Mapped[User | None] = relationship("User", foreign_keys=[owner_user_id])
    created_by: Mapped[User | None] = relationship("User", foreign_keys=[created_by_user_id])
