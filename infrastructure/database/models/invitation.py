"""
Invitations let a tenant admin add a second person to an EXISTING
tenant. Without this, POST /auth/register always creates a brand new
tenant + OWNER, so a workspace could never grow past its first user --
a real gap found in this project's own product audit (see
CHANGELOG.md).

Token is opaque (secrets.token_urlsafe), stored hashed (sha256) here --
same principle as RefreshToken: a database leak alone must not hand out
a usable invite. The raw token goes out exactly once, in the
POST /tenant/invites response, for the admin to send however they like
(Slack, email client, whatever) -- this project doesn't send email
itself yet, that's real future work.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infrastructure.database.models.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from infrastructure.database.models.rbac import Role
    from infrastructure.database.models.tenant import Tenant
    from infrastructure.database.models.user import User


class Invitation(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "invitations"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The one address allowed to accept this invite -- accept-invite
    # never lets the caller choose a different email than this.
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    # SET NULL, not CASCADE: the inviting admin leaving later must not
    # delete an invite someone else still hasn't acted on.
    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped[Tenant] = relationship("Tenant")
    role: Mapped[Role] = relationship("Role")
    invited_by: Mapped[User | None] = relationship("User")
