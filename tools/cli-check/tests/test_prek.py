"""Exercise the configured hooks with real prek and isolated Git repositories."""

import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.integration


@pytest.fixture
def hook_repo(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    assert shutil.which("prek"), "Install prek to run hook integration tests"
    local = subprocess.check_output(
        ["git", "rev-parse", "--local-env-vars"], text=True
    ).splitlines()
    env = {key: value for key, value in os.environ.items() if key not in local}
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        PREK_HOME=str(tmp_path / "cache"),
        TRACE=str(tmp_path / "trace"),
    )
    subprocess.run(["git", "init", "-q", str(tmp_path)], env=env, check=True)
    return tmp_path, env


def configure(root: Path, *ids: str) -> None:
    config = tomllib.loads((ROOT / "prek.toml").read_text())
    hooks = [h for repo in config["repos"] for h in repo["hooks"] if h["id"] in ids]
    assert {h["id"] for h in hooks} == set(ids)
    (root / ".pre-commit-config.yaml").write_text(
        json.dumps(
            {
                "repos": [{"repo": "local", "hooks": hooks}],
                "default_stages": ["pre-commit"],
            }
        )
    )


def git(root: Path, env: dict[str, str], *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, env=env, check=True, capture_output=True)


def run(root: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["prek", "run", "--config", ".pre-commit-config.yaml", *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def stub_tools(root: Path, env: dict[str, str], *names: str) -> dict[str, str]:
    directory = root / "bin"
    directory.mkdir()
    for name in names:
        path = directory / name
        path.write_text(
            f"#!{sys.executable}\nimport json, os, pathlib, sys\n"
            "with open(os.environ['TRACE'], 'a') as trace:\n"
            "    trace.write(json.dumps([pathlib.Path(sys.argv[0]).name, *sys.argv[1:]]) + '\\n')\n"
        )
        path.chmod(0o755)
    return env | {"PATH": str(directory) + os.pathsep + env["PATH"]}


def test_python_tests_skip_rust_hooks_but_rust_fixtures_select_them(
    hook_repo: tuple[Path, dict[str, str]],
) -> None:
    root, env = hook_repo
    configure(root, "cargo-clippy", "cargo-nexttest", "cli-check")
    env = stub_tools(root, env, "uv")
    for name in ("packages/boxie/tests/test_app.py", "buildins/echor/tests/inputs/data.txt"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture")
    result = run(root, env, "--files", "packages/boxie/tests/test_app.py")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (root / "trace").exists()
    result = run(root, env, "--files", "buildins/echor/tests/inputs/data.txt")
    assert result.returncode == 0, result.stdout + result.stderr
    assert len((root / "trace").read_text().splitlines()) == 3


@pytest.mark.parametrize("config_file", ["lychee.toml", "typos.toml", "prek.toml"])
def test_documentation_config_scans_tracked_markdown(
    hook_repo: tuple[Path, dict[str, str]],
    config_file: str,
) -> None:
    root, env = hook_repo
    configure(root, "lychee", "typos")
    env = stub_tools(root, env, "lychee", "typos")
    (root / "a b.md").write_text("# Hello\n")
    (root / "untracked.md").write_text("# Ignored\n")
    (root / config_file).write_text("")
    git(root, env, "add", "a b.md", config_file)
    result = run(root, env, "--files", config_file)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = [json.loads(line) for line in (root / "trace").read_text().splitlines()]
    assert {call[0] for call in calls} == (
        {"lychee"} if config_file == "lychee.toml" else {"lychee", "typos"}
    )
    assert all(call[-1] == "a b.md" and "untracked.md" not in call for call in calls)


def test_deleted_link_target_is_rejected_without_editing_markdown(
    hook_repo: tuple[Path, dict[str, str]],
) -> None:
    assert shutil.which("lychee"), "Install lychee to run link integration tests"
    root, env = hook_repo
    configure(root, "lychee")
    (root / "README.md").write_text("![Logo](logo.svg)\n")
    (root / "logo.svg").write_text("<svg/>\n")
    git(root, env, "add", "README.md", "logo.svg")
    git(
        root,
        env,
        "-c",
        "user.name=Tests",
        "-c",
        "user.email=tests@example.invalid",
        "commit",
        "-qm",
        "fixture",
    )
    git(root, env, "rm", "logo.svg")
    result = run(root, env)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "logo.svg" in result.stdout + result.stderr


def test_mixed_code_and_config_runs_each_checker_once(
    hook_repo: tuple[Path, dict[str, str]],
) -> None:
    root, env = hook_repo
    configure(root, "ruff", "ruff-format", "actionlint", "zizmor")
    env = stub_tools(root, env, "uv", "actionlint", "zizmor")
    names = [
        "app.py",
        "pyproject.toml",
        ".github/workflows/test.yml",
        ".github/actionlint.yaml",
        "zizmor.yml",
    ]
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    result = run(root, env, "--files", *names)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = [json.loads(line) for line in (root / "trace").read_text().splitlines()]
    assert len(calls) == 4
    assert sum(call[0] == "uv" for call in calls) == 2
    assert all("app.py" not in call for call in calls)
