"""Alembic entry point using only Playground's metadata."""

from alembic import context
from cli_playground.config import DatabaseSettings
from cli_playground.tables import Table
from sqlalchemy import engine_from_config, pool

config = context.config
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option(
        "sqlalchemy.url", str(DatabaseSettings().database_url).replace("%", "%%")
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
