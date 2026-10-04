"""
Per-tenant overrides of AgentGuard's built-in per-connector default
policy (connectors/gmail/policy.py, connectors/github/policy.py) -- e.g.
"read_message requires approval" is the system default every tenant's
Gmail connector runs under today, but a given tenant should be able to
tighten it further (DENY it outright) or loosen it (ALLOW without
approval) for their own agents. See docs/PRODUCTIZATION_ROADMAP.md,
Phase A -- this is the "Policy Engine" gap that document names as the
platform's top priority.

A PolicyRule can only act on an action the connector's own Policy
already has in its task_scope (agentguard/policy.py) -- that scope is a
structural capability boundary ("can this connector do this at all"),
not a business rule, and no tenant override can widen it; see
apps/api/routers/connectors/general.py's execute_connector_action for
where this is actually evaluated, and apps/api/routers/policies.py for
where a tenant-wide rule is created/edited (RequireTenantAdmin only --
letting a mere OPERATOR loosen their own agent's approval requirement
would defeat the point).

Phase B (docs/PRODUCTIZATION_ROADMAP.md) added agent_id: NULL means
"applies to every agent in this tenant" (a Phase A rule, unchanged
meaning); set, it applies only to that one Agent
(infrastructure/database/models/agent.py) and wins over the tenant-wide
row for the same (connector_type, action) -- see
apps/api/routers/agents.py's effective-policy resolution. Two agents (or
an agent and the tenant-wide default) can both have a rule for the same
action at once, so the uniqueness constraint can't be a single plain
UniqueConstraint the way Phase A's was: Postgres treats NULL != NULL, so
a plain UniqueConstraint including agent_id would let multiple
tenant-wide (agent_id IS NULL) rows through for the same action instead
of enforcing "at most one." Same problem, and same fix, as
infrastructure/database/models/rbac.py's system-role partial unique
index -- two partial unique indexes below, one for agent_id IS NULL, one
for agent_id IS NOT NULL.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infrastructure.database.models.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from infrastructure.database.models.agent import Agent
    from infrastructure.database.models.user import User

# Plain strings, not a SQLAlchemy Enum column -- matches
# ApprovalRequest.status/risk_level's existing convention (see
# infrastructure/database/models/approval.py) rather than introducing a
# second one. Validated at the API boundary (apps/api/routers/policies.py's
# Literal-typed request model), not the DB layer.
POLICY_DECISIONS = ("ALLOW", "DENY", "REQUIRE_APPROVAL")


class PolicyRule(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "policy_rules"
    __table_args__ = (
        Index(
            "uq_policy_rule_tenant_wide",
            "tenant_id",
            "connector_type",
            "action",
            unique=True,
            postgresql_where=text("agent_id IS NULL"),
        ),
        Index(
            "uq_policy_rule_agent_scoped",
            "tenant_id",
            "connector_type",
            "action",
            "agent_id",
            unique=True,
            postgresql_where=text("agent_id IS NOT NULL"),
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connector_type: Mapped[str] = mapped_column(String(50), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), nullable=True, index=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    agent: Mapped[Agent | None] = relationship("Agent")
    created_by: Mapped[User | None] = relationship("User")
