"""Package selection and hook execution without running compilers or whole suites."""

import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def write(root: Path, name: str, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    write(tmp_path, "Cargo.toml", '[workspace]\nmembers = ["crates/*"]\n')
    for name in ("one", "two", "shared"):
        write(tmp_path, f"crates/{name}/Cargo.toml", f'[package]\nname = "{name}"\n')
        write(tmp_path, f"crates/{name}/src/lib.rs", "")
    write(
        tmp_path,
        "Cargo.lock",
        'version = 4\n[[package]]\nname = "one"\nversion = "0.1.0"\n'
        'dependencies = ["shared"]\n[[package]]\nname = "two"\nversion = "0.1.0"\n'
        '[[package]]\nname = "shared"\nversion = "0.1.0"\n',
    )
    write(
        tmp_path,
        "pyproject.toml",
        '[tool.pytest]\ntestpaths = ["apps/api/tests", "apps/client/tests", "apps/other/tests"]\n',
    )
    for name in ("api", "client", "other"):
        write(tmp_path, f"apps/{name}/tests/test_app.py", "")
    write(
        tmp_path,
        "uv.lock",
        '[[package]]\nname = "api"\nsource = { editable = "apps/api" }\n'
        '[[package]]\nname = "client"\nsource = { editable = "apps/client" }\n'
        'dependencies = [{name = "api"}]\n'
        '[[package]]\nname = "other"\nsource = { virtual = "apps/other" }\n',
    )
    return tmp_path


def plan(root: Path, hook: str, *paths: str) -> list[list[str]]:
    from cli_check.hooks import commands

    return commands(root, hook, list(paths))


def test_python_test_never_runs_rust_checks(workspace: Path) -> None:
    for hook in ("cargo-fmt", "cargo-clippy", "cargo-nexttest", "cli-check"):
        assert plan(workspace, hook, "apps/api/tests/test_app.py") == []
    assert plan(workspace, "python-tests", "apps/api/tests/deleted.py") == [
        ["uv", "run", "pytest", "apps/api/tests"]
    ]


def test_rust_source_includes_consumers_but_fixture_only_checks_owner(workspace: Path) -> None:
    assert plan(workspace, "cargo-nexttest", "crates/shared/src/lib.rs") == [
        [
            "cargo",
            "nextest",
            "run",
            "-p",
            "one",
            "-p",
            "shared",
            "--hide-progress-bar",
            "--failure-output",
            "final",
        ]
    ]
    command = plan(workspace, "cargo-clippy", "crates/one/tests/inputs/deleted.txt")[0]
    assert command[command.index("-p") + 1] == "one"
    assert command.count("-p") == 1
    assert "--workspace" not in command


def test_python_source_checks_consumers_without_unrelated_package(workspace: Path) -> None:
    assert plan(workspace, "python-tests", "apps/api/src/api.py") == [
        ["uv", "run", "pytest", "apps/api/tests", "apps/client/tests"]
    ]
    assert plan(workspace, "mypy", "apps/api/tests/test_app.py") == [
        ["uv", "run", "mypy", "apps/api"]
    ]


def test_shared_configs_select_only_their_language(workspace: Path) -> None:
    assert plan(workspace, "cargo-nexttest", "pyproject.toml") == []
    assert plan(workspace, "python-tests", "Cargo.toml") == []
    assert plan(workspace, "cargo-clippy", "Cargo.toml")[0].count("-p") == 3
    assert plan(workspace, "python-tests", "uv.lock")[0][-3:] == [
        "apps/api/tests",
        "apps/client/tests",
        "apps/other/tests",
    ]
    assert plan(workspace, "ruff-config", "apps/api/pyproject.toml") == [
        ["uv", "run", "ruff", "check", "apps/api"]
    ]
    assert plan(workspace, "cli-check", "README.md")[0].count("-p") == 3
    assert plan(workspace, "cargo-fmt", "crates/one/src/lib.rs") == [
        ["cargo", "fmt", "-p", "one", "--", "--check"]
    ]


def test_real_repository_reset_test_is_isolated() -> None:
    path = "infra/environments/single-host/tests/test_reset.py"
    assert plan(ROOT, "python-tests", path) == [
        ["uv", "run", "pytest", "infra/environments/single-host/tests"]
    ]
    assert plan(ROOT, "cargo-nexttest", path) == []
    assert plan(ROOT, "cargo-clippy", path) == []


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "pyproject.toml",
        "uv.lock",
        "tools/cli-check/src/cli_check/workspace.py",
        "tools/cli-check/tests/test_hooks.py",
    ],
)
def test_checker_changes_select_structural_checks_only(workspace: Path, path: str) -> None:
    assert plan(workspace, "cli-check", path) == [
        ["uv", "run", "cli-check", "-p", "one", "-p", "shared", "-p", "two"]
    ]
    for hook in ("cargo-fmt", "cargo-clippy", "cargo-nexttest"):
        assert plan(workspace, hook, path) == []


def test_repository_tooling_and_shared_test_support_include_consumers() -> None:
    command = plan(ROOT, "python-tests", "tools/cli-check/src/cli_check/workspace.py")[0]
    assert "tools/cli-bench/tests" in command
    assert "tools/cli-playground/tests" in command
    assert "packages/boxie/tests" not in command
    command = plan(
        ROOT, "python-tests", "packages/boxie/tests/support/boxie_test_support/terminal_network.py"
    )[0]
    assert "packages/boxie/tests" in command
    assert "tools/cli-playground/tests" in command
    assert "tools/cli-bench/tests" not in command
    command = plan(ROOT, "python-tests", "infra/host/tests/host_mocks.py")[0]
    assert "infra/host/tests" in command
    assert "infra/environments/single-host/tests" in command


def test_no_tests_directory_does_not_fall_back_to_bare_pytest(workspace: Path) -> None:
    (workspace / "apps/api/tests/test_app.py").unlink()
    (workspace / "apps/api/tests").rmdir()
    assert plan(workspace, "python-tests", "apps/api/tests/deleted.py") == []


def test_hook_config_passes_paths_and_serializes_package_checks() -> None:
    config = tomllib.loads((ROOT / "prek.toml").read_text())
    for repo in config["repos"]:
        for hook in repo["hooks"]:
            if "cli_check.hooks" in hook.get("entry", ""):
                assert hook.get("pass_filenames", True)
                assert hook["require_serial"]
    rust = next(h for r in config["repos"] for h in r["hooks"] if h["id"] == "cargo-clippy")
    assert not re.search(rust["files"], "infra/environments/single-host/tests/test_reset.py")


def test_runner_preserves_failure_and_no_selection_runs_nothing(workspace: Path) -> None:
    bin_dir = workspace / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "cargo"
    stub.write_text(
        f"#!{sys.executable}\nimport json, pathlib, sys\n"
        "pathlib.Path('called.json').write_text(json.dumps(sys.argv[1:]))\nsys.exit(7)\n"
    )
    stub.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    result = subprocess.run(
        [sys.executable, "-m", "cli_check.hooks", "cargo-nexttest", "crates/one/src/lib.rs"],
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 7, result.stderr
    assert json.loads((workspace / "called.json").read_text())[2:4] == ["-p", "one"]
    (workspace / "called.json").unlink()
    result = subprocess.run(
        [sys.executable, "-m", "cli_check.hooks", "cargo-nexttest", "apps/api/tests/test_app.py"],
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert not (workspace / "called.json").exists()


def test_mypy_resolves_workspace_dependencies_without_adding_them_as_targets(
    workspace: Path,
) -> None:
    write(workspace, "apps/api/src/api/__init__.py", "")
    stub = workspace / "bin/uv"
    write(
        workspace,
        "bin/uv",
        f"#!{sys.executable}\nimport json, os, pathlib, sys\n"
        "pathlib.Path('mypy.json').write_text(json.dumps("
        "{'args': sys.argv[1:], 'paths': os.environ.get('MYPYPATH', '').split(os.pathsep)}))\n",
    )
    stub.chmod(0o755)
    env = dict(os.environ, PATH=f"{stub.parent}{os.pathsep}{os.environ['PATH']}")
    result = subprocess.run(
        [sys.executable, "-m", "cli_check.hooks", "mypy", "apps/client/tests/test_app.py"],
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    captured = json.loads((workspace / "mypy.json").read_text())
    assert captured["args"] == ["run", "mypy", "apps/client"]
    assert str(workspace / "apps/api/src") in captured["paths"]
    assert str(workspace / "apps/api/tests") in captured["paths"]
