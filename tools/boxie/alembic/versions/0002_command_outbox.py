"""Replace dispatcher scheduling state with a transactional command handoff."""

import sqlalchemy as sa
from alembic import op

revision = "0002_command_outbox"
down_revision = "0001_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    outbox = sa.table("outbox", sa.column("kind", sa.String()))
    op.execute(sa.delete(outbox).where(outbox.c.kind == "observation"))
    op.drop_index("outbox_pending", table_name="outbox")
    op.drop_constraint("outbox_lease", "outbox", type_="check")
    op.drop_constraint("outbox_kind", "outbox", type_="check")
    for name in ("attempts", "next_attempt_at", "lease_owner", "lease_expires_at"):
        op.drop_column("outbox", name)
    op.create_check_constraint("outbox_kind", "outbox", sa.column("kind").in_(["start", "stop"]))
    op.create_index(
        "outbox_pending",
        "outbox",
        ["created_at"],
        postgresql_where=sa.column("delivered_at").is_(None),
    )


def downgrade() -> None:
    raise RuntimeError("command outbox downgrade is unsupported")
