"""seed system roles and permissions

Revision ID: e8c125103dc7
Revises: 648f90089832
Create Date: 2026-08-19 05:34:46.858306

"""
"""
Data migration -- deliberately uses sa.table()/sa.column() lightweight
reflections rather than importing the ORM models directly, so this
migration stays correct even if the models change shape later (the
usual Alembic data-migration convention: migrations are a frozen
record, not living code coupled to today's model definitions).

Least-privilege role -> permission mapping (first draft, adjustable):
  VIEWER   : *.read only
  OPERATOR : VIEWER + agent.execute, connector.write
  APPROVER : VIEWER + approval.approve
  ADMIN    : OPERATOR + APPROVER + policy.write
  OWNER    : ADMIN + tenant.admin, billing.admin
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8c125103dc7'
down_revision: Union[str, Sequence[str], None] = '648f90089832'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PERMISSIONS = [
    "connector.read",
    "connector.write",
    "agent.read",
    "agent.execute",
    "policy.read",
    "policy.write",
    "approval.read",
    "approval.approve",
    "audit.read",
    "tenant.admin",
    "billing.admin",
]

_READ_ONLY = {"connector.read", "agent.read", "policy.read", "approval.read", "audit.read"}
_OPERATOR = _READ_ONLY | {"agent.execute", "connector.write"}
_APPROVER = _READ_ONLY | {"approval.approve"}
_ADMIN = _OPERATOR | _APPROVER | {"policy.write"}
_OWNER = _ADMIN | {"tenant.admin", "billing.admin"}

ROLES = {
    "VIEWER": _READ_ONLY,
    "OPERATOR": _OPERATOR,
    "APPROVER": _APPROVER,
    "ADMIN": _ADMIN,
    "OWNER": _OWNER,
}


def _tables():
    permissions = sa.table(
        "permissions", sa.column("id", sa.Uuid), sa.column("code", sa.String),
        sa.column("description", sa.String),
    )
    roles = sa.table(
        "roles", sa.column("id", sa.Uuid), sa.column("tenant_id", sa.Uuid),
        sa.column("name", sa.String), sa.column("is_system_role", sa.Boolean),
        sa.column("created_at", sa.DateTime), sa.column("updated_at", sa.DateTime),
    )
    role_permissions = sa.table(
        "role_permissions", sa.column("role_id", sa.Uuid), sa.column("permission_id", sa.Uuid),
    )
    return permissions, roles, role_permissions


def upgrade() -> None:
    permissions, roles, role_permissions = _tables()
    now = sa.func.now()

    permission_ids = {code: uuid.uuid4() for code in PERMISSIONS}
    op.bulk_insert(
        permissions,
        [{"id": permission_ids[code], "code": code, "description": ""} for code in PERMISSIONS],
    )

    role_ids = {name: uuid.uuid4() for name in ROLES}
    op.execute(
        roles.insert().values(
            [
                {
                    "id": role_ids[name],
                    "tenant_id": None,
                    "name": name,
                    "is_system_role": True,
                    "created_at": now,
                    "updated_at": now,
                }
                for name in ROLES
            ]
        )
    )

    op.bulk_insert(
        role_permissions,
        [
            {"role_id": role_ids[role_name], "permission_id": permission_ids[perm_code]}
            for role_name, perm_codes in ROLES.items()
            for perm_code in perm_codes
        ],
    )


def downgrade() -> None:
    permissions, roles, role_permissions = _tables()
    op.execute(role_permissions.delete())
    op.execute(roles.delete().where(roles.c.name.in_(list(ROLES))))
    op.execute(permissions.delete().where(permissions.c.code.in_(PERMISSIONS)))
