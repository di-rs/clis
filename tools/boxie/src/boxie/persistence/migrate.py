"""Explicit, checksum-verified SQL releases; never invoked by API startup."""

import argparse
import hashlib
import re
from pathlib import Path

import psycopg

LOCK = 0x426F7869654D6967


def migrate(dsn: str, migrations: Path) -> None:
    files = sorted(migrations.glob("*.sql"))
    versions: dict[int, tuple[Path, str]] = {}
    for path in files:
        match = re.fullmatch(r"(\d+)_[a-z0-9_]+\.sql", path.name)
        if match is None or int(match[1]) in versions:
            raise ValueError("migrations require unique numbered SQL files")
        versions[int(match[1])] = (path, hashlib.sha256(path.read_bytes()).hexdigest())
    if not versions:
        raise ValueError("no migrations found")
    with psycopg.connect(dsn, autocommit=True, connect_timeout=10) as conn:
        conn.execute("SET lock_timeout = '20s'")
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK,))
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                version integer PRIMARY KEY, checksum text NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT clock_timestamp())""")
            applied: dict[int, str] = dict(
                conn.execute("SELECT version, checksum FROM schema_migrations")
            )
            for version, checksum in applied.items():
                if version not in versions or versions[version][1] != checksum:
                    raise ValueError(f"migration {version} checksum mismatch or missing file")
            for version, (path, checksum) in sorted(versions.items()):
                if version in applied:
                    continue
                if applied and version < max(applied):
                    raise ValueError("cannot insert a migration before an applied version")
                with conn.transaction():
                    conn.execute(path.read_text())
                    conn.execute(
                        "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                        (version, checksum),
                    )
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK,))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn-file", type=Path, required=True)
    parser.add_argument("--migrations", type=Path, default=Path(__file__).with_name("migrations"))
    args = parser.parse_args()
    migrate(args.dsn_file.read_text().strip(), args.migrations)


if __name__ == "__main__":
    main()
