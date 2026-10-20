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
    command.upgrade(config, "0002_command_outbox:0004_observability_cleanup", sql=True)
    schema = output.getvalue()
    assert "ADD COLUMN provisioning_outcome" in schema
    assert "ADD COLUMN provisioning_ready_observed_at" in schema
    assert "ADD COLUMN cleanup_pending_since" in schema
    assert "sandbox_provisioning_outcome" in schema
    assert "DROP CONSTRAINT provisioning_ready_evidence" in schema
    assert "DROP COLUMN provisioning_ready_observed_at" in schema
    assert "CREATE INDEX sandbox_cleanup_pending" in schema
    assert "UPDATE sandboxes" not in schema


@pytest.mark.integration
def test_forward_observability_revision_removes_unread_timestamp(postgres_dsn: str) -> None:
    from boxie.persistence.database import database_url
    from sqlalchemy import create_engine, inspect

    config = migration_config(postgres_dsn)
    command.upgrade(config, "0002_command_outbox")
    command.upgrade(config, "0003_observability")
    command.upgrade(config, "head")
    engine = create_engine(database_url(postgres_dsn))
    try:
        columns = {column["name"] for column in inspect(engine).get_columns("sandboxes")}
        assert "provisioning_ready_observed_at" not in columns
        assert {"provisioning_outcome", "cleanup_pending_since"} <= columns
        command.check(config)
    finally:
        engine.dispose()


@pytest.mark.integration
def test_request_correlation_migration_preserves_existing_rows(
    postgres_dsn: str, tmp_path: Path
) -> None:
    import asyncio

    from boxie.persistence.models import Outbox, Sandbox
    from database_support import OWNER, database, upload
    from sqlmodel import select

    async def seed() -> object:
        async with database(postgres_dsn, tmp_path) as (life, artifacts, _, _):
            return (await life.submit(OWNER, "before-upgrade", await upload(artifacts))).id

    identifier = asyncio.run(seed())
    config = migration_config(postgres_dsn)
    command.downgrade(config, "0005_component_names")
    command.upgrade(config, "head")

    async def verify() -> None:
        async with database(postgres_dsn, tmp_path) as (_, _, sessions, _):
            async with sessions() as session:
                row = await session.get(Sandbox, identifier)
                assert (
                    row is not None
                    and row.creation_request_id is None
                    and row.stop_request_id is None
                )
                commands = (
                    await session.exec(select(Outbox).where(Outbox.sandbox_id == identifier))
                ).all()
                assert len(commands) == 1 and commands[0].request_id is None

    asyncio.run(verify())
    command.check(config)
