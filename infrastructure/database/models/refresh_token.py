"""
Refresh tokens are stored HASHED (sha256), never in plaintext -- same
principle as passwords: a database leak must not directly hand out
valid tokens. sha256 (not Argon2) is fine here because a refresh token
is a 256-bit random value, not a human-chosen low-entropy secret --
there is nothing for a slow KDF to protect against.

Rotation + revocation: every /auth/refresh call marks the presented
token revoked_at=now and issues a brand new one, so a stolen-then-used
refresh token can't be replayed by whoever had it first, and a user
can always be logged out everywhere by revoking their tokens.
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


class RefreshToken(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship("User", back_populates="refresh_tokens")
