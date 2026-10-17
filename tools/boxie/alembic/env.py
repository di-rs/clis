"""Alembic's standard online/offline environment, packaged with Boxie."""

import os
from pathlib import Path

from alembic import context
from boxie.persistence import models as models
from boxie.persistence.database import database_url
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

config = context.config
if not config.get_main_option("sqlalchemy.url") and (
    dsn_file := os.environ.get("BOXIE_POSTGRES_DSN_FILE")
):
    config.set_main_option(
        "sqlalchemy.url",
        database_url(Path(dsn_file).read_text().strip())
        .render_as_string(hide_password=False)
        .replace("%", "%%"),
    )


def run_migrations() -> None:
    if context.is_offline_mode():
        context.configure(
            url=config.get_main_option("sqlalchemy.url"),
            target_metadata=SQLModel.metadata,
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
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=SQLModel.metadata)
            with context.begin_transaction():
                context.run_migrations()
        engine.dispose()


run_migrations()
