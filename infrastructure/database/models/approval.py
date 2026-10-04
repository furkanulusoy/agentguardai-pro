"""
Persisted approval requests -- when AgentGuard's policy marks an action
"sensitive" (agentguard/policy.py's sensitive_actions), the platform
records one of these instead of executing immediately. Nothing blocks
waiting for a decision: the row IS the state machine. A human resolves
it later (possibly from a different process than the one that created
it, possibly after this process has restarted) and the real guarded
call happens at that moment, inside the resolve request -- see
apps/api/routers/approvals.py and apps/api/services/execution.py.
result_json/error_message hold the outcome of that deferred call once
it has actually run, so a caller polling this row (the dashboard, an
MCP server) can retrieve it after the fact instead of a live connection
having to still be open.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infrastructure.database.models.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from infrastructure.database.models.agent import Agent
    from infrastructure.database.models.credential import Credential
    from infrastructure.database.models.tenant import Tenant
    from infrastructure.database.models.user import User


class ApprovalRequest(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "approval_requests"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    credential_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("credentials.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requested_by_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Which Agent (infrastructure/database/models/agent.py) actually
    # called execute -- NULL when a human called it directly (dashboard's
    # own "test an action" button, or a personal JWT). requested_by_user_id
    # above stays populated either way: it's the agent's owner when an
    # agent made the call, or the calling human themself otherwise -- "who
    # is accountable" always has an answer even when "which agent" doesn't.
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    # JSON-encoded {"args": [...], "kwargs": {...}} -- informational
    # only, shown on the dashboard so a human can see what they're
    # approving. Connector actions built so far take no secrets as
    # arguments (e.g. Gmail's message_id); this is not a safe place to
    # put one.
    call_context: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False, default="MEDIUM")
    # PENDING | APPROVED | DENIED | EXPIRED
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING", index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Populated only after an APPROVED request's deferred guard.call()
    # actually runs (apps/api/routers/approvals.py's resolve_approval).
    # Exactly one of these is set once execution has happened; both stay
    # NULL for a still-PENDING, DENIED, or EXPIRED request.
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set by apps/api/services/notifications.py if a GitHub notification
    # issue was opened for this approval -- snapshotted at creation time
    # (not read live from Tenant.notification_repo), so resolving this
    # request later still closes the right issue even if the tenant's
    # notification repo setting has since changed. Both NULL if
    # notifications were off, or the notification attempt failed --
    # notifying is always best-effort, never blocks the approval itself.
    notification_repo: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notification_issue_number: Mapped[int | None] = mapped_column(nullable=True)

    tenant: Mapped[Tenant] = relationship("Tenant")
    credential: Mapped[Credential] = relationship("Credential")
    requested_by: Mapped[User] = relationship("User", foreign_keys=[requested_by_user_id])
    resolved_by: Mapped[User | None] = relationship("User", foreign_keys=[resolved_by_user_id])
    agent: Mapped[Agent | None] = relationship("Agent")
