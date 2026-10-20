"""Alembic entry point using only Playground's metadata."""

import os
from pathlib import Path

from alembic import context
from cli_playground.database import database_url, read_dsn
from cli_playground.tables import Table
from sqlalchemy import engine_from_config, pool

config = context.config
if not config.get_main_option("sqlalchemy.url"):
    dsn = read_dsn(Path(os.environ["CLI_PLAYGROUND_POSTGRES_DSN_FILE"]))
    config.set_main_option(
        "sqlalchemy.url", database_url(dsn).render_as_string(hide_password=False).replace("%", "%%")
    )
if context.is_offline_mode():
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=Table.metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=Table.metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()
