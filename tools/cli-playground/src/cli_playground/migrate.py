"""Explicit PostgreSQL schema upgrade, run before starting the gateway."""

import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config

from cli_playground.database import database_url, read_dsn


def migration_config(dsn: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "alembic"))
    config.set_main_option(
        "sqlalchemy.url", database_url(dsn).render_as_string(hide_password=False).replace("%", "%%")
    )
    return config


def migrate(dsn: str) -> None:
    command.upgrade(migration_config(dsn), "head")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn-file", type=Path, required=True)
    args = parser.parse_args()
    migrate(read_dsn(args.dsn_file))


if __name__ == "__main__":
    main()
