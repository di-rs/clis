"""Chromium E2Es with an isolated real PostgreSQL database."""

import os
import subprocess
from pathlib import Path

import pytest

from .helpers import migrate

pytestmark = pytest.mark.integration


def test_chromium(postgres_dsn: str, tmp_path: Path) -> None:
    migrate(postgres_dsn)
    subprocess.run(
        ["pnpm", "exec", "playwright", "test"],
        cwd=Path(__file__).parents[1] / "frontend",
        env={**os.environ, "CLI_PLAYGROUND_DATABASE_URL": postgres_dsn},
        check=True,
        timeout=240,
    )
