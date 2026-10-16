"""An installed migration package can render the full schema without a database."""

from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from integration.conftest import postgres_dsn as postgres_dsn
from integration.conftest import postgres_server as postgres_server


def test_initial_revision_renders_schema_and_capacity_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    import boxie.persistence

    # An explicit migration target must win over an unrelated operator environment.
    monkeypatch.setenv("BOXIE_POSTGRES_DSN_FILE", "/missing/unrelated-database-secret")
    output = StringIO()
    config = Config(output_buffer=output)
    config.set_main_option(
        "script_location", str(Path(boxie.persistence.__file__).with_name("migrations"))
    )
    config.set_main_option("sqlalchemy.url", "postgresql+psycopg://localhost/boxie")
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
