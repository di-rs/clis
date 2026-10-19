"""Remove unread telemetry detail while preserving lifecycle resolution and cleanup age."""

from alembic import op

revision = "0004_observability_cleanup"
down_revision = "0003_observability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("provisioning_ready_evidence", "sandboxes", type_="check")
    op.drop_column("sandboxes", "provisioning_ready_observed_at")


def downgrade() -> None:
    raise NotImplementedError("discarded readiness timestamps cannot be reconstructed")
