"""Durable execution ledger and explicit resource grants."""
from alembic import op
import sqlalchemy as sa
revision = "c002_durable_operations"
down_revision = "c001_identity_security"
branch_labels = None
depends_on = None
def upgrade():
    op.add_column("agent_credential_grants", sa.Column("resource_scope", sa.Text(), nullable=False, server_default="[]"))
    op.create_table("operations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_id", sa.Uuid(), sa.ForeignKey("agents.id", ondelete="SET NULL")),
        sa.Column("credential_id", sa.Uuid(), sa.ForeignKey("credentials.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_key", sa.String(80), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("encrypted_payload", sa.Text(), nullable=False),
        sa.Column("encrypted_result", sa.Text()),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.Column("decision_source", sa.String(40), nullable=False),
        sa.Column("policy_fingerprint", sa.String(64), nullable=False),
        sa.Column("approval_id", sa.Uuid(), sa.ForeignKey("approval_requests.id", ondelete="SET NULL"), unique=True),
        sa.Column("audit_id", sa.Uuid(), sa.ForeignKey("audit_events.id", ondelete="SET NULL")),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("tenant_id", "actor_key", "idempotency_key", name="uq_operation_idempotency"),
        sa.CheckConstraint("status IN ('READY','WAITING','EXECUTING','SUCCEEDED','FAILED','UNKNOWN','DENIED','EXPIRED')", name="ck_operation_status"),
    )
    op.create_index("ix_operations_tenant_id", "operations", ["tenant_id"])
    op.create_index("ix_operations_status", "operations", ["status"])
    op.create_check_constraint("ck_policy_decision", "policy_rules", "decision IN ('ALLOW','DENY','REQUIRE_APPROVAL')")
def downgrade():
    op.drop_constraint("ck_policy_decision", "policy_rules")
    op.drop_table("operations")
    op.drop_column("agent_credential_grants", "resource_scope")
