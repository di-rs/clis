"""Installed commands remain usable outside a source checkout."""

import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("command", ["cli-bench", "cli-bench-select", "cli-bench-report"])
def test_installed_command_help(command: str, tmp_path: Path) -> None:
    result = subprocess.run(
        [command, "--help"], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout
