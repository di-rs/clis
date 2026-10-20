"""Initial Playground metadata schema, independent of Boxie."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001_metadata"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "preview",
        sa.Column("pr", sa.BigInteger(), primary_key=True),
        sa.Column("id", sa.String(), nullable=False, unique=True),
        sa.Column("closed", sa.Boolean(), nullable=False),
        sa.Column("expires_at", sa.Float()),
        sa.Column("build_sha", sa.String()),
        sa.Column("run_id", sa.BigInteger()),
        sa.Column("run_number", sa.BigInteger()),
        sa.Column("run_attempt", sa.BigInteger()),
        sa.Column("build_state", sa.String()),
        sa.Column("current_revision", sa.String()),
        sa.CheckConstraint("pr > 0", name="positive_pr"),
        sa.CheckConstraint(
            "num_nonnulls(build_state, build_sha, run_id, run_number, run_attempt) = 0 OR (num_nonnulls(build_state, build_sha, run_id, run_number, run_attempt) = 5 AND build_state IN ('building','failed','empty','cancelled','ready') AND run_id > 0 AND run_number > 0 AND run_attempt > 0)",
            name="valid_build",
        ),
    )
    op.create_index("ix_preview_id", "preview", ["id"])
    op.create_table(
        "artifact_revision",
        sa.Column("pr", sa.BigInteger(), sa.ForeignKey("preview.pr"), primary_key=True),
        sa.Column("revision", sa.String(), primary_key=True),
        sa.Column("artifact_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("sha", sa.String(), nullable=False),
        sa.Column("sha256", sa.String(), nullable=False),
        sa.Column("clis", JSONB(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
    )
    op.create_foreign_key(
        "preview_current_revision",
        "preview",
        "artifact_revision",
        ["pr", "current_revision"],
        ["pr", "revision"],
    )
    op.create_table(
        "session_binding",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("preview_id", sa.String(), sa.ForeignKey("preview.id"), nullable=False),
        sa.Column("revision", sa.String(), nullable=False),
        sa.Column("credential_hash", sa.String(), nullable=False),
        sa.Column("sandbox", JSONB(), nullable=False),
        sa.Column("pending_stop", sa.Boolean(), nullable=False),
        sa.Column("attachment_deadline", sa.Float()),
        sa.Column("attached", sa.Boolean(), nullable=False),
        sa.Column("attachment_claim", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_session_binding_preview_id", "session_binding", ["preview_id"])
    op.create_index("ix_session_binding_pending_stop", "session_binding", ["pending_stop"])
    op.create_table(
        "create_intent",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("preview_id", sa.String(), sa.ForeignKey("preview.id"), nullable=False),
        sa.Column("revision", sa.String(), nullable=False),
        sa.Column("credential_hash", sa.String(), nullable=False),
        sa.Column("spec", JSONB(), nullable=False),
        sa.Column("claim_until", sa.Float(), nullable=False),
        sa.Column("abandoned", sa.Boolean(), nullable=False),
    )
    op.create_table(
        "upload_intent",
        sa.Column("artifact_id", sa.Uuid(), primary_key=True),
        sa.Column("pr", sa.BigInteger(), sa.ForeignKey("preview.pr"), nullable=False),
        sa.Column("revision", sa.String(), nullable=False),
        sa.Column("sha", sa.String(), nullable=False),
        sa.Column("sha256", sa.String(), nullable=False),
        sa.Column("clis", JSONB(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("build_sha", sa.String()),
        sa.Column("run_id", sa.BigInteger()),
        sa.Column("run_number", sa.BigInteger()),
        sa.Column("run_attempt", sa.BigInteger()),
        sa.Column("previous_revision", sa.String()),
        sa.Column("cancelled", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("pr", "revision"),
    )
    op.create_index("ix_upload_intent_pr", "upload_intent", ["pr"])
    op.create_table(
        "artifact_retirement",
        sa.Column("artifact_id", sa.Uuid(), primary_key=True),
        sa.Column("completed", sa.Boolean(), nullable=False),
    )


def downgrade() -> None:
    op.drop_constraint("preview_current_revision", "preview", type_="foreignkey")
    for table in (
        "artifact_retirement",
        "upload_intent",
        "create_intent",
        "session_binding",
        "artifact_revision",
        "preview",
    ):
        op.drop_table(table)
