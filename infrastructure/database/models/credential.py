"""
Stores connector credentials (OAuth tokens, API keys) ENCRYPTED at rest
-- infrastructure/secrets.encrypt() writes here, .decrypt() reads back.
The database itself never sees plaintext; a DB leak alone does not hand
out working credentials without also having SECRET_ENCRYPTION_KEY.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infrastructure.database.models.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from infrastructure.database.models.tenant import Tenant
    from infrastructure.database.models.user import User


class Credential(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "credentials"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Who connected this -- nullable because some connectors (e.g. a
    # Slack workspace bot token) are tenant-wide, not tied to whichever
    # person happened to set them up. Deleting that user must not
    # silently break a shared integration, so this is SET NULL, not
    # CASCADE.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Matches BaseConnector.connector_type (connectors/base.py), e.g. "gmail".
    connector_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    encrypted_secret: Mapped[str] = mapped_column(Text, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped[Tenant] = relationship("Tenant")
    user: Mapped[User | None] = relationship("User")
