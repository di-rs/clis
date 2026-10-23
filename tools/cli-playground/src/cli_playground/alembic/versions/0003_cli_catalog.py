"""Keep command catalogs with immutable revisions and recoverable uploads."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_cli_catalog"
down_revision = "0002_request_correlation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("artifact_revision", "upload_intent"):
        op.add_column(table, sa.Column("catalog", postgresql.JSONB(), nullable=True))
        op.add_column(table, sa.Column("catalog_sha256", sa.String(), nullable=True))


def downgrade() -> None:
    for table in ("upload_intent", "artifact_revision"):
        op.drop_column(table, "catalog_sha256")
        op.drop_column(table, "catalog")
