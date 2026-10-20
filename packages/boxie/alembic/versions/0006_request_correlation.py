"""Preserve diagnostic action origins without changing existing retry identity."""

import sqlalchemy as sa
from alembic import op

revision = "0006_request_correlation"
down_revision = "0005_component_names"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sandboxes", sa.Column("creation_request_id", sa.Uuid(), nullable=True))
    op.add_column("sandboxes", sa.Column("stop_request_id", sa.Uuid(), nullable=True))
    op.add_column("outbox", sa.Column("request_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.drop_column("outbox", "request_id")
    op.drop_column("sandboxes", "stop_request_id")
    op.drop_column("sandboxes", "creation_request_id")
