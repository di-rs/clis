"""An installed migration package can render the full schema without a database."""

from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from boxie.persistence.migrate import migration_config


@pytest.mark.parametrize("operator_config", [False, True])
def test_initial_revision_renders_schema_and_capacity_seed(
    monkeypatch: pytest.MonkeyPatch, operator_config: bool
) -> None:
    # An explicit migration target must win over an unrelated operator environment.
    monkeypatch.setenv("BOXIE_POSTGRES_DSN_FILE", "/missing/unrelated-database-secret")
    output = StringIO()
    config = migration_config("postgresql://localhost/boxie")
    if operator_config:
        config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", "postgresql+psycopg://localhost/boxie")
    config.output_buffer = output
    command.upgrade(config, "head", sql=True)
    schema = output.getvalue()
    assert "CREATE TABLE sandboxes" in schema
    assert "CREATE TABLE alembic_version" in schema
    assert "UNIQUE (consumer_id, idempotency_key)" in schema
    assert schema.count("INSERT INTO capacity_slots") == 8


@pytest.mark.integration
def test_initial_revision_matches_mapped_metadata(postgres_dsn: str) -> None:
    from boxie.persistence.migrate import migrate, migration_config

    migrate(postgres_dsn)
    command.check(migration_config(postgres_dsn))


def test_observability_revision_preserves_unknown_cleanup_age() -> None:
    output = StringIO()
    config = migration_config("postgresql://localhost/boxie")
    config.output_buffer = output
    command.upgrade(config, "0002_command_outbox:head", sql=True)
    schema = output.getvalue()
    assert "ADD COLUMN provisioning_outcome" in schema
    assert "ADD COLUMN provisioning_ready_observed_at" in schema
    assert "ADD COLUMN cleanup_pending_since" in schema
    assert "sandbox_provisioning_outcome" in schema
    assert "provisioning_ready_evidence" in schema
    assert "CREATE INDEX sandbox_cleanup_pending" in schema
    assert "UPDATE sandboxes" not in schema
