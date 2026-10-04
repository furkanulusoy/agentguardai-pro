"""
Which specific Credentials (infrastructure/database/models/credential.py)
an Agent is actually allowed to act against -- the "its own scoped
credential grants (which connectors, which actions)" piece
Phase B's own original sketch in docs/PRODUCTIZATION_ROADMAP.md named
and didn't build; the "which actions" half landed there (PolicyRule.
agent_id), this closes the "which connectors" half.

Deny-by-default, matching this project's own established RBAC
philosophy (apps/api/dependencies.py's own docstring: "a user with zero
roles... is rejected -- there is no implicit 'everyone can' fallback"):
an Agent with zero grants can act on NOTHING, not everything. This is a
real, intentional tightening from Phase B's original shape (where an
Agent could execute against any credential its owner could see) -- an
Agent created before this phase needs an explicit grant before it can
execute anything again. See apps/api/routers/connectors/general.py's
execute_connector_action for where this is enforced (checked before
task_scope, the same structural-ceiling position), and
apps/api/routers/agents.py for where a grant is created/removed
(RequireTenantAdmin only).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infrastructure.database.models.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from infrastructure.database.models.agent import Agent
    from infrastructure.database.models.credential import Credential
    from infrastructure.database.models.user import User


class AgentCredentialGrant(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "agent_credential_grants"
    __table_args__ = (
        UniqueConstraint("agent_id", "credential_id", name="uq_agent_credential_grant"),
    )

    resource_scope: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    credential_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("credentials.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    agent: Mapped[Agent] = relationship("Agent")
    credential: Mapped[Credential] = relationship("Credential")
    created_by: Mapped[User | None] = relationship("User")
