"""
A real record of every governance decision POST /connectors/{id}/execute
(apps/api/routers/connectors/general.py) makes -- not just the ones that
happened to need a human's approval. See docs/PRODUCTIZATION_ROADMAP.md,
Phase C: before this, an auto-ALLOWed action (the common case -- most
actions aren't sensitive) left literally zero trace anywhere once it
ran; ApprovalRequest only ever existed for the REQUIRE_APPROVAL path.

decision_source answers the "why" Phase A's own writeup named as a real
gap it was deliberately NOT closing yet ("a DENY/REQUIRE_APPROVAL
response today is a bare error/approval record... it doesn't yet say
*which* rule produced the outcome"):
  - "AGENT_NOT_GRANTED" -- the acting Agent has no
                          AgentCredentialGrant for this credential at
                          all (agent_credential_grant.py) -- rejected
                          before task_scope or any PolicyRule is even
                          looked up; never applies to a human calling
                          directly.
  - "TASK_SCOPE"      -- the connector doesn't support this action at
                          all (agentguard/policy.py's task_scope) --
                          rejected before any PolicyRule is even looked up.
  - "AGENT_POLICY"     -- an agent-scoped PolicyRule matched.
  - "TENANT_POLICY"    -- a tenant-wide PolicyRule matched (no
                          agent-scoped override for this actor).
  - "SYSTEM_DEFAULT"   -- no PolicyRule at all; the connector's own
                          built-in Policy.sensitive_actions decided it.

For a REQUIRE_APPROVAL row, approval_id points at the ApprovalRequest
that actually carries the eventual outcome (approved/denied/expired,
result/error) -- deliberately NOT duplicated onto this row too, so
there is exactly one place that state can drift out of sync (none).
For an ALLOW row, result_json/error_message are populated directly here
-- since execution happens synchronously in that path (Phase 7), this
is the only record of it that will ever exist. A DENY row (either
source) never executes anything, so both stay NULL.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infrastructure.database.models.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from infrastructure.database.models.agent import Agent
    from infrastructure.database.models.approval import ApprovalRequest
    from infrastructure.database.models.credential import Credential
    from infrastructure.database.models.user import User


class AuditEvent(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "audit_events"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    credential_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("credentials.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The accountable human -- the agent's owner, or the calling human
    # directly. Same convention as ApprovalRequest.requested_by_user_id.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    # Same informational-only, not-yet-redacted caveat as
    # ApprovalRequest.call_context -- see that model's own comment.
    call_context: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    decision: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    decision_source: Mapped[str] = mapped_column(String(20), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False, default="MEDIUM")
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("approval_requests.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Same not-yet-redacted caveat as call_context above -- str(exc)
    # verbatim, visible to anyone with approval.read. Not known to leak
    # anything today (checked against this session's own real
    # PyGithub/google-auth exceptions), but no redaction exists if a
    # future connector's exception types did.
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    credential: Mapped[Credential] = relationship("Credential")
    user: Mapped[User] = relationship("User")
    agent: Mapped[Agent | None] = relationship("Agent")
    approval: Mapped[ApprovalRequest | None] = relationship("ApprovalRequest")
