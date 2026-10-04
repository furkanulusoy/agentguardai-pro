"""
Admin-initiated password resets -- an OWNER/ADMIN generates a one-time
link for a locked-out teammate to set a new password, the same "show
the raw token once, admin sends it manually" shape as
infrastructure/database/models/invitation.py (this project doesn't
send real email yet -- see that model's own docstring).

Deliberately NOT a self-service "forgot password" flow: proving "the
person on this device is actually the account owner" before they're
authenticated requires an out-of-band channel (real email, at
minimum), which this project doesn't have. A solo OWNER locked out of
their own only account has no admin to ask -- that gap is real, stated
here rather than glossed over, and tracked in
docs/ROADMAP_TO_PRODUCTION.md as needing real email delivery.
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


class PasswordReset(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "password_resets"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    # SET NULL, not CASCADE: the admin who triggered this leaving later
    # must not delete a reset link the target person hasn't used yet.
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship("User", foreign_keys=[user_id])
    requested_by: Mapped[User | None] = relationship("User", foreign_keys=[requested_by_user_id])
