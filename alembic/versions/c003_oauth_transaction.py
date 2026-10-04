"""Single-use OAuth browser transactions."""
from alembic import op
import sqlalchemy as sa
revision="c003_oauth_transaction"
down_revision="c002_durable_operations"
branch_labels=None
depends_on=None
def upgrade():
    op.create_table("oauth_transactions",
        sa.Column("id",sa.Uuid(),primary_key=True),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("state_hash",sa.String(64),unique=True,nullable=False),
        sa.Column("nonce_hash",sa.String(64),nullable=False),
        sa.Column("user_id",sa.Uuid(),sa.ForeignKey("users.id",ondelete="CASCADE"),nullable=False),
        sa.Column("tenant_id",sa.Uuid(),sa.ForeignKey("tenants.id",ondelete="CASCADE"),nullable=False),
        sa.Column("expires_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("used_at",sa.DateTime(timezone=True)))
def downgrade():
    op.drop_table("oauth_transactions")
