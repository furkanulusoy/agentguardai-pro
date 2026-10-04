from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infrastructure.database.models.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from infrastructure.database.models.user import User


class Tenant(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # URL-safe unique identifier (e.g. subdomain, invite links) -- distinct
    # from the display name, which a tenant can change freely.
    slug: Mapped[str] = mapped_column(String(63), unique=True, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # "owner/repo" -- where apps/api/services/notifications.py opens a
    # GitHub Issue when a sensitive action creates a PENDING
    # ApprovalRequest. NULL means notifications are off (the default);
    # set via PATCH /tenant/settings. Uses the tenant's own connected
    # GitHub credential -- no separate notification-specific OAuth.
    notification_repo: Mapped[str | None] = mapped_column(String(200), nullable=True)

    users: Mapped[list[User]] = relationship(
        "User", back_populates="tenant", cascade="all, delete-orphan"
    )
