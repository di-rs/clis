"""Preserve originating requests across asynchronous recovery."""

import sqlalchemy as sa
from alembic import op

revision = "0002_request_correlation"
down_revision = "0001_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("session_binding", sa.Column("stop_request_id", sa.Uuid(), nullable=True))
    for table in ("create_intent", "upload_intent", "artifact_retirement"):
        op.add_column(table, sa.Column("request_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    for table in ("artifact_retirement", "upload_intent", "create_intent"):
        op.drop_column(table, "request_id")
    op.drop_column("session_binding", "stop_request_id")
