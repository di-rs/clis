"""Temporary Git commits must never inherit the checkout's hook context."""

import os
import subprocess
from pathlib import Path

import pytest

from .helpers import git_commit


def test_fixture_commit_preserves_outer_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")} | {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    outer = tmp_path / "outer.git"
    inner = tmp_path / "inner"
    subprocess.run(["git", "init", "--bare", "-q", str(outer)], env=clean, check=True)
    subprocess.run(["git", "init", "-q", str(inner)], env=clean, check=True)
    (inner / "file").write_text("fixture")
    subprocess.run(["git", "-C", str(inner), "add", "."], env=clean, check=True)
    before = (outer / "config").read_bytes()
    index = outer / "index"
    index.write_bytes(b"outer-index-sentinel")
    monkeypatch.setenv("GIT_DIR", str(outer))
    monkeypatch.setenv("GIT_INDEX_FILE", str(index))
    git_commit(inner)
    head = subprocess.check_output(
        ["git", "-C", str(inner), "rev-parse", "HEAD"], env=clean, text=True
    ).strip()
    assert len(head) == 40
    assert (outer / "config").read_bytes() == before
    assert index.read_bytes() == b"outer-index-sentinel"
