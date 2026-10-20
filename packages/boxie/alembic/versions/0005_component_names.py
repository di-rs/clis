"""Rename drained lifecycle state while preserving ownership and retry identity."""

import hashlib
import json

from alembic import context, op
from sqlalchemy import text

revision = "0005_component_names"
down_revision = "0004_observability_cleanup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "LOCK TABLE sandboxes, artifacts, capacity_slots, outbox, "
        "sandbox_artifact_refs, orchestrator_observations IN ACCESS EXCLUSIVE MODE"
    )
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM sandboxes WHERE state NOT IN ('terminated', 'failed')
                     OR cleaned_at IS NULL OR cleanup_pending)
             OR EXISTS (SELECT 1 FROM capacity_slots WHERE sandbox_id IS NOT NULL)
             OR EXISTS (SELECT 1 FROM sandbox_artifact_refs)
             OR EXISTS (SELECT 1 FROM outbox WHERE delivered_at IS NULL)
             OR EXISTS (SELECT 1 FROM artifacts WHERE collection_pending)
             OR EXISTS (SELECT 1 FROM orchestrator_observations
                        WHERE resource_present OR cleanup_pending OR NOT removal_confirmed)
          THEN RAISE EXCEPTION 'drain sandboxes, commands and artifact collection before migration';
          END IF;
          IF EXISTS (SELECT 1 FROM sandboxes WHERE consumer_id = 'playground')
             OR EXISTS (SELECT 1 FROM artifacts WHERE consumer_id = 'playground')
          THEN
            IF EXISTS (SELECT 1 FROM sandboxes WHERE consumer_id = 'cli-playground')
               OR EXISTS (SELECT 1 FROM artifacts WHERE consumer_id = 'cli-playground')
            THEN RAISE EXCEPTION 'conflicting playground and cli-playground ownership'; END IF;
          END IF;
        END $$
    """)
    if context.is_offline_mode():
        # Request hashing uses Python's canonical JSON encoding, including Unicode escaping.
        op.execute("""
            DO $$ BEGIN
              IF EXISTS (SELECT 1 FROM sandboxes
                         WHERE consumer_id = 'playground' OR spec->>'profile_id' = 'playground')
              THEN RAISE EXCEPTION 'use the online Alembic upgrade for legacy request hashes';
              END IF;
            END $$
        """)
    else:
        connection = op.get_bind()
        rows = (
            connection.execute(
                text("""
            SELECT id, consumer_id, spec, snapshot FROM sandboxes
            WHERE consumer_id = 'playground' OR spec->>'profile_id' = 'playground'
        """)
            )
            .mappings()
            .all()
        )
        for row in rows:
            spec, snapshot = dict(row["spec"]), dict(row["snapshot"])
            if spec.get("profile_id") == "playground":
                spec["profile_id"] = "cli-playground"
            if row["consumer_id"] == "playground" and spec.get("command") == [
                "/opt/playground/shell.sh"
            ]:
                spec["command"] = ["/opt/cli-playground/shell.sh"]
            request_hash = hashlib.sha256(
                json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            connection.execute(
                text("""
                UPDATE sandboxes SET spec = CAST(:spec AS jsonb), snapshot = CAST(:snapshot AS jsonb),
                                     request_hash = :request_hash WHERE id = :id
            """),
                {
                    "spec": json.dumps(spec),
                    "snapshot": json.dumps(snapshot),
                    "request_hash": request_hash,
                    "id": row["id"],
                },
            )
    op.execute(
        "UPDATE sandboxes SET consumer_id = 'cli-playground' WHERE consumer_id = 'playground'"
    )
    op.execute(
        "UPDATE artifacts SET consumer_id = 'cli-playground' WHERE consumer_id = 'playground'"
    )
    op.execute("""
        UPDATE sandboxes SET reason = CASE reason
          WHEN 'orchestrator_restart' THEN 'supervisor_restart'
          WHEN 'orchestrator_shutdown' THEN 'supervisor_shutdown' END
        WHERE reason IN ('orchestrator_restart', 'orchestrator_shutdown')
    """)
    op.rename_table("orchestrator_observations", "supervisor_observations")
    op.execute(
        "ALTER TABLE supervisor_observations RENAME CONSTRAINT "
        "orchestrator_observations_pkey TO supervisor_observations_pkey"
    )
    op.execute(
        "ALTER TABLE supervisor_observations RENAME CONSTRAINT "
        "orchestrator_observations_sandbox_id_fkey TO supervisor_observations_sandbox_id_fkey"
    )
    op.alter_column("sandboxes", "orchestrator_boot_id", new_column_name="supervisor_boot_id")


def downgrade() -> None:
    raise NotImplementedError("restore the matching pre-cutover database and journal backups")
