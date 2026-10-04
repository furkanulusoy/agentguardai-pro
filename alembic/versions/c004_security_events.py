"""Transactional management audit trail."""
from alembic import op
import sqlalchemy as sa
revision="c004_security_events"
down_revision="c003_oauth_transaction"
branch_labels=None
depends_on=None
def upgrade():
    op.create_table("security_events",
        sa.Column("id",sa.Uuid(),primary_key=True),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("tenant_id",sa.Uuid(),sa.ForeignKey("tenants.id",ondelete="CASCADE"),nullable=False),
        sa.Column("actor_id",sa.Uuid(),sa.ForeignKey("users.id",ondelete="SET NULL")),
        sa.Column("action",sa.String(80),nullable=False),
        sa.Column("target",sa.String(200),nullable=False))
    op.create_index("ix_security_events_tenant_id","security_events",["tenant_id"])
def downgrade():
    op.drop_table("security_events")
