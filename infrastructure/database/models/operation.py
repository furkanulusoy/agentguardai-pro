"""Durable execution ownership. UNKNOWN is never automatically retried."""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.database.models.base import Base, TimestampMixin, UUIDPKMixin


class Operation(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "operations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "actor_key", "idempotency_key", name="uq_operation_idempotency"
        ),
        CheckConstraint(
            "status IN ('READY','WAITING','EXECUTING','SUCCEEDED',"
            "'FAILED','UNKNOWN','DENIED','EXPIRED')",
            name="ck_operation_status",
        ),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    credential_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("credentials.id", ondelete="CASCADE")
    )
    actor_key: Mapped[str] = mapped_column(String(80))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    payload_hash: Mapped[str] = mapped_column(String(64))
    encrypted_payload: Mapped[str] = mapped_column(Text)
    encrypted_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), index=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    decision_source: Mapped[str] = mapped_column(String(40))
    policy_fingerprint: Mapped[str] = mapped_column(String(64))
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("approval_requests.id", ondelete="SET NULL"), unique=True, nullable=True
    )
    audit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("audit_events.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
