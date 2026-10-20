"""Chromium E2Es with an isolated real PostgreSQL database."""

import os
import subprocess
from pathlib import Path

import pytest
from cli_playground.migrate import migrate

from .helpers import protected

pytestmark = pytest.mark.integration


def test_chromium(postgres_dsn: str, tmp_path: Path) -> None:
    migrate(postgres_dsn)
    secret = protected(tmp_path / "postgres-dsn", postgres_dsn.encode())
    subprocess.run(
        ["pnpm", "exec", "playwright", "test"],
        cwd=Path(__file__).parents[1] / "frontend",
        env={**os.environ, "CLI_PLAYGROUND_POSTGRES_DSN_FILE": str(secret)},
        check=True,
        timeout=240,
    )
