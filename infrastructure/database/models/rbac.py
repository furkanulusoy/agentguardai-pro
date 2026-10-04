"""
RBAC: Role <-> Permission (many-to-many via role_permissions), User <->
Role (many-to-many via user_roles -- a user can hold more than one
role). Deny > approval > allow is enforced at the policy-engine layer
(agentguard/guardrail.py's own precedence), not here -- this module only
answers "what is this user allowed to do at all," not "does this
specific action need approval."
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Column, ForeignKey, Index, String, Table, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from infrastructure.database.models.base import Base, TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from infrastructure.database.models.user import User

role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column("role_id", ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("permission_id", ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
)

user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)


class Permission(UUIDPKMixin, Base):
    __tablename__ = "permissions"

    # e.g. "connector.read", "approval.approve", "tenant.admin"
    code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    description: Mapped[str] = mapped_column(String(300), nullable=False, default="")


class Role(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "roles"

    # NULL tenant_id = a system-defined role (OWNER/ADMIN/APPROVER/OPERATOR/
    # VIEWER), available to every tenant. Non-NULL would be a future
    # tenant-custom role -- not built in this step, but the column already
    # supports it so that migration won't need a schema change later.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    is_system_role: Mapped[bool] = mapped_column(default=False, nullable=False)

    permissions: Mapped[list[Permission]] = relationship(
        Permission, secondary=role_permissions
    )
    users: Mapped[list[User]] = relationship(
        "User", secondary=user_roles, back_populates="roles"
    )

    __table_args__ = (
        # Postgres treats NULL != NULL, so a plain UniqueConstraint on
        # (tenant_id, name) would NOT stop two system roles (tenant_id
        # IS NULL) from sharing a name -- a partial index is the correct
        # way to enforce "at most one system role per name" while still
        # allowing different tenants to each have their own "Ops"-named
        # custom role later.
        Index(
            "uq_roles_system_role_name",
            "name",
            unique=True,
            postgresql_where=text("tenant_id IS NULL"),
        ),
    )
