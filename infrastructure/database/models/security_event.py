"""Management audit metadata: never accepts free-form request bodies or credentials."""

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.database.models.base import Base, TimestampMixin, UUIDPKMixin


class SecurityEvent(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "security_events"
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(80))
    target: Mapped[str] = mapped_column(String(200))
