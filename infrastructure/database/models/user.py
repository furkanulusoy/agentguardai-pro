from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infrastructure.database.models.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from infrastructure.database.models.rbac import Role
    from infrastructure.database.models.refresh_token import RefreshToken
    from infrastructure.database.models.tenant import Tenant


class User(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "users"

    auth_version: Mapped[int] = mapped_column(default=0, nullable=False)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Globally unique, not per-tenant: login identifies a user by email
    # alone (no separate tenant-slug field), and this model gives each
    # user exactly one tenant (no cross-tenant membership table) -- so a
    # per-tenant unique constraint would only create an ambiguous login
    # (which tenant's "admin@company.com"?) for no real benefit. Revisit
    # if/when a user-can-belong-to-multiple-tenants model is built.
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="users")
    roles: Mapped[list[Role]] = relationship("Role", secondary="user_roles", back_populates="users")
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        "RefreshToken", back_populates="user", cascade="all, delete-orphan"
    )
