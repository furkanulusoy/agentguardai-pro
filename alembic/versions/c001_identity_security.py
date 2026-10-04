"""Explicit identity and session invalidation."""
from alembic import op
import sqlalchemy as sa
revision = "c001_identity_security"
down_revision = "1130f09ac23e"
branch_labels = None
depends_on = None
def upgrade():
    op.add_column("users", sa.Column("auth_version", sa.Integer(), server_default="0", nullable=False))
    # Existing zero-grant agents intentionally become denied. Administrators must grant explicitly.
def downgrade():
    op.drop_column("users", "auth_version")
