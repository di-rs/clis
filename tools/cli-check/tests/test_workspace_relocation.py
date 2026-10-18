"""Root check configuration keeps covering projects after directory moves."""

import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from cli_check.workspace import find_root


def test_root_checks_follow_projects_after_move(tmp_path: Path) -> None:
    config = tomllib.loads((find_root(Path.cwd()) / "pyproject.toml").read_text())
    baseline = tmp_path / "tools/cli-check/tests"
    baseline.mkdir(parents=True)
    (baseline / "test_baseline.py").write_text("def test_baseline() -> None: pass\n")
    for name in ("moved_a", "moved_b"):
        project = tmp_path / name
        project.mkdir()
        (project / "__init__.py").write_text("")
        (project / "test_moved.py").write_text("def test_moved() -> None: pass\n")
    (tmp_path / "moved_a/broken.py").write_text('value: int = "wrong"\n')
    (tmp_path / "pyproject.toml").write_text(
        "[tool.pytest]\ntestpaths = "
        + json.dumps(config["tool"]["pytest"]["testpaths"])
        + "\n[tool.mypy]\nstrict = true\nfiles = "
        + json.dumps(config["tool"]["mypy"]["files"])
        + "\n"
    )
    tests = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert tests.returncode == 0, tests.stdout + tests.stderr
    assert "3 passed" in tests.stdout
    types = subprocess.run(
        [sys.executable, "-m", "mypy"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert types.returncode == 1, types.stdout + types.stderr
    assert "moved_a/broken.py" in types.stdout


@pytest.mark.integration
def test_real_workspace_survives_directory_moves(tmp_path: Path) -> None:
    root = find_root(Path.cwd())
    relocated = tmp_path / "renamed checkout"
    relocated.mkdir()
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
    for value in filter(None, paths):
        source, destination = root / value, relocated / value
        if not source.exists() and not source.is_symlink():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination, follow_symlinks=False)
    moves = {
        "tools/boxie": "boxie",
        "tools/cli-bench": "dev/bench/cli-bench",
        "coreutils/echor": "commands/text/echor",
        "utils/cli-log": "shared/logging/cli-log",
    }
    for old, new in moves.items():
        destination = relocated / new
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(relocated / old), destination)
    python_manifest = relocated / "pyproject.toml"
    python_manifest.write_text(
        python_manifest.read_text()
        .replace("tools/boxie", "boxie")
        .replace("tools/cli-bench", "dev/bench/cli-bench")
    )
    cargo = relocated / "Cargo.toml"
    cargo.write_text(
        cargo.read_text()
        .replace('"coreutils/*"', '"coreutils/*", "commands/text/echor", "shared/logging/cli-log"')
        .replace('path = "utils/cli-log"', 'path = "shared/logging/cli-log"')
    )
    env = {
        key: value for key, value in os.environ.items() if key not in {"VIRTUAL_ENV", "PYTHONPATH"}
    }

    env["CARGO_TARGET_DIR"] = str(root / "target")

    def run(*args: str) -> str:
        result = subprocess.run(
            args, cwd=relocated, env=env, capture_output=True, text=True, timeout=300, check=False
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    run(
        "uv",
        "lock",
        "--offline",
        "-P",
        "boxie",
        "-P",
        "boxie-infra",
        "-P",
        "boxie-test-support",
        "-P",
        "cli-bench",
    )
    run("uv", "sync", "--locked", "--all-packages", "--offline")
    assert Path(run("uv", "workspace", "dir", "--package", "boxie").strip()) == relocated / "boxie"
    run("cargo", "metadata", "--locked", "--offline", "--format-version", "1", "--no-deps")
    run("cargo", "check", "--locked", "--offline", "-p", "echor")
    run("uv", "run", "--locked", "mypy")
    run("uv", "run", "--locked", "pytest", "--collect-only", "-q")
    run("uv", "run", "--locked", "cli-bench-select", "--help")
