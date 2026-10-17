"""Explicit Alembic schema upgrades; never invoked by API startup."""

import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config

from boxie.persistence.database import database_url


def migration_config(dsn: str, migrations: Path | None = None) -> Config:
    config = Config()
    source = Path(__file__).resolve().parents[3] / "alembic"
    default = source if source.is_dir() else Path(__file__).parents[1] / "alembic"
    config.set_main_option("script_location", str(migrations or default))
    config.set_main_option(
        "sqlalchemy.url", database_url(dsn).render_as_string(hide_password=False).replace("%", "%%")
    )
    return config


def migrate(dsn: str, migrations: Path | None = None) -> None:
    command.upgrade(migration_config(dsn, migrations), "head")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn-file", type=Path, required=True)
    args = parser.parse_args()
    migrate(args.dsn_file.read_text().strip())


if __name__ == "__main__":
    main()
