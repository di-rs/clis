"""Retain immutable provisioning resolution and SQL-known cleanup demand age."""

import sqlalchemy as sa
from alembic import op

revision = "0003_observability"
down_revision = "0002_command_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sandboxes", sa.Column("provisioning_outcome", sa.String(), nullable=True))
    op.add_column(
        "sandboxes",
        sa.Column("provisioning_ready_observed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sandboxes", sa.Column("cleanup_pending_since", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        "sandbox_provisioning_outcome",
        "sandboxes",
        sa.column("provisioning_outcome").in_(["ready", "failed", "stopped"]),
    )
    op.create_check_constraint(
        "provisioning_ready_evidence",
        "sandboxes",
        sa.or_(
            sa.and_(
                sa.column("provisioning_outcome").is_not_distinct_from("ready"),
                sa.column("provisioning_ready_observed_at").is_not(None),
            ),
            sa.and_(
                sa.column("provisioning_outcome").is_distinct_from("ready"),
                sa.column("provisioning_ready_observed_at").is_(None),
            ),
        ),
    )
    op.create_index(
        "sandbox_cleanup_pending",
        "sandboxes",
        ["cleanup_pending_since"],
        postgresql_where=sa.column("cleanup_pending"),
    )
    # Existing pending rows retain an unknown age; migration time is not demand time.


def downgrade() -> None:
    op.drop_index("sandbox_cleanup_pending", table_name="sandboxes")
    op.drop_constraint("provisioning_ready_evidence", "sandboxes", type_="check")
    op.drop_constraint("sandbox_provisioning_outcome", "sandboxes", type_="check")
    for name in ("cleanup_pending_since", "provisioning_ready_observed_at", "provisioning_outcome"):
        op.drop_column("sandboxes", name)
