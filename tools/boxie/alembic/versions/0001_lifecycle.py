"""Initial lifecycle authority, including fenced observations and outbox leases."""

from datetime import timedelta

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_lifecycle"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("consumer_id", sa.String(), nullable=False),
        sa.Column("digest", sa.String(), nullable=False),
        sa.Column("storage_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("retired", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("collection_pending", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("collected_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.CheckConstraint(
            sa.column("digest").regexp_match("^[a-f0-9]{64}$"), name="artifact_digest"
        ),
    )
    op.create_table(
        "sandboxes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("consumer_id", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("request_hash", sa.String(), nullable=False),
        sa.Column("spec", postgresql.JSONB(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("installation_id", sa.Uuid(), nullable=False),
        sa.Column("host_id", sa.String(), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False, server_default=sa.literal(1)),
        sa.Column("orchestrator_boot_id", sa.Uuid()),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("desired_stop", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.String()),
        sa.Column("state", sa.String(), nullable=False, server_default=sa.literal("accepted")),
        sa.Column("cleanup_pending", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cleaned_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("consumer_id", "idempotency_key"),
        sa.CheckConstraint(sa.column("generation") > 0, name="sandbox_generation"),
        sa.CheckConstraint(
            sa.column("state").in_(
                ["accepted", "provisioning", "ready", "terminating", "terminated", "failed"]
            ),
            name="sandbox_state",
        ),
        sa.CheckConstraint(
            sa.and_(
                sa.column("absolute_deadline") > sa.column("accepted_at"),
                sa.column("absolute_deadline") <= sa.column("accepted_at") + timedelta(hours=1),
            ),
            name="sandbox_deadline",
        ),
        sa.CheckConstraint(
            sa.or_(
                sa.column("cleaned_at").is_(None),
                sa.and_(
                    ~sa.column("cleanup_pending"),
                    sa.column("state").in_(["terminated", "failed"]),
                ),
            ),
            name="sandbox_cleaned",
        ),
    )
    op.create_table(
        "capacity_slots",
        sa.Column("slot", sa.Integer(), primary_key=True),
        sa.Column(
            "sandbox_id", sa.Uuid(), sa.ForeignKey("sandboxes.id", ondelete="RESTRICT"), unique=True
        ),
        sa.CheckConstraint(sa.column("slot").between(1, 8), name="capacity_range"),
    )
    op.bulk_insert(
        sa.table("capacity_slots", sa.column("slot", sa.Integer())),
        [{"slot": slot} for slot in range(1, 9)],
    )
    op.create_table(
        "sandbox_artifact_refs",
        sa.Column(
            "sandbox_id",
            sa.Uuid(),
            sa.ForeignKey("sandboxes.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "artifact_id",
            sa.Uuid(),
            sa.ForeignKey("artifacts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
    )
    op.create_index("artifact_references", "sandbox_artifact_refs", ["artifact_id"])
    op.create_table(
        "outbox",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("deduplication_key", sa.String(), nullable=False, unique=True),
        sa.Column(
            "sandbox_id",
            sa.Uuid(),
            sa.ForeignKey("sandboxes.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.literal(0)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.clock_timestamp(),
        ),
        sa.Column("lease_owner", sa.String()),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            sa.column("kind").in_(["start", "stop", "observation"]), name="outbox_kind"
        ),
        sa.CheckConstraint(
            sa.column("lease_owner").is_(None) == sa.column("lease_expires_at").is_(None),
            name="outbox_lease",
        ),
    )
    op.create_index(
        "outbox_pending",
        "outbox",
        ["next_attempt_at"],
        postgresql_where=sa.column("delivered_at").is_(None),
    )
    op.create_table(
        "orchestrator_observations",
        sa.Column(
            "sandbox_id",
            sa.Uuid(),
            sa.ForeignKey("sandboxes.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("boot_id", sa.Uuid(), nullable=False),
        sa.Column("event_version", sa.BigInteger(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True)),
        sa.Column("last_input_at", sa.DateTime(timezone=True)),
        sa.Column("disconnected_at", sa.DateTime(timezone=True)),
        sa.Column("resource_present", sa.Boolean(), nullable=False),
        sa.Column("removal_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cleanup_pending", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "attachment_epoch", sa.BigInteger(), nullable=False, server_default=sa.literal(0)
        ),
        sa.Column("absolute_deadline", sa.DateTime(timezone=True)),
        sa.Column("control_lease_until", sa.DateTime(timezone=True)),
        sa.Column("container_id", sa.String()),
        sa.CheckConstraint(sa.column("event_version") > 0, name="observation_version"),
        sa.CheckConstraint(
            sa.or_(
                ~sa.column("removal_confirmed"),
                sa.and_(~sa.column("resource_present"), ~sa.column("cleanup_pending")),
            ),
            name="observation_removal",
        ),
    )


def downgrade() -> None:
    for table in (
        "orchestrator_observations",
        "outbox",
        "sandbox_artifact_refs",
        "capacity_slots",
        "sandboxes",
        "artifacts",
    ):
        op.drop_table(table)
