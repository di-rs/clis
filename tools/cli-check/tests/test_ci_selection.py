"""Exercise CI selection boundaries, real Git diffs and the required-check gate."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from ci.select_jobs import JOBS, cargo_directories, select_jobs
from ci.test_features import feature_packages

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("docs/guide.md", set()),
        ("buildins/echor/tests/inputs/empty", {"rust", "dependencies", "codeql_rust"}),
        ("utils/cli-testing/src/lib.rs", {"rust", "dependencies", "codeql_rust"}),
        ("Cargo.lock", {"rust", "dependencies", "codeql_rust"}),
        (".cargo/config.toml", {"rust", "dependencies", "codeql_rust"}),
        ("packages/new/src/lib.rs", {"rust", "dependencies", "codeql_rust"}),
        ("packages/boxie/tests/data/request.json", {"python", "frontend"}),
        ("tools/cli-playground/frontend/pnpm-lock.yaml", {"python", "frontend"}),
        ("uv.lock", {"python", "frontend"}),
        ("infra/host/src/host_infra/bootstrap.sh", {"python"}),
        (".github/workflows/codeql.yml", {"python", "codeql_rust", "codeql_actions"}),
        ("deny.toml", {"dependencies"}),
        ("tools/ci/select_jobs.py", set(JOBS)),
        ("prek.toml", set(JOBS)),
        (".github/workflows/pull-request-check.yml", set(JOBS)),
    ],
)
def test_required_jobs_for_independent_input_types(path: str, expected: set[str]) -> None:
    selected = select_jobs([path], ("buildins/echor/", "utils/cli-testing/"))
    assert {name for name, enabled in selected.items() if enabled} == expected


def test_feature_tests_exclude_featureless_members_and_nonmembers() -> None:
    assert feature_packages(
        {
            "workspace_members": ["simple", "optional"],
            "packages": [
                {"id": "simple", "name": "echo", "features": {}},
                {"id": "optional", "name": "generator", "features": {"format": []}},
                {"id": "external", "name": "external", "features": {"unused": []}},
            ],
        }
    ) == ["generator"]


@pytest.fixture
def selection_repo(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    local = subprocess.check_output(
        ["git", "rev-parse", "--local-env-vars"], text=True
    ).splitlines()
    env = {key: value for key, value in os.environ.items() if key not in local}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    env.update(GIT_AUTHOR_NAME="CI test", GIT_AUTHOR_EMAIL="test@example.invalid")
    env.update(GIT_COMMITTER_NAME="CI test", GIT_COMMITTER_EMAIL="test@example.invalid")
    script = tmp_path / "tools/ci/select_jobs.py"
    script.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "tools/ci/select_jobs.py", script)
    (tmp_path / "Cargo.toml").write_text('[workspace]\nmembers = ["buildins/*"]\n')
    fixture = tmp_path / "buildins/echo/tests/inputs/data\nwith-newline"
    fixture.parent.mkdir(parents=True)
    fixture.write_text("input")
    (tmp_path / "buildins/echo/Cargo.toml").write_text('[package]\nname = "echo"\n')
    git(tmp_path, env, "init", "-q")
    git(tmp_path, env, "add", ".")
    git(tmp_path, env, "commit", "-qm", "base")
    return tmp_path, env


def git(root: Path, env: dict[str, str], *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, env=env, text=True).strip()


def run_selector(
    root: Path, env: dict[str, str], event: str, payload: dict[str, object], *args: str
) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
    event_file, output = root / "event.json", root / "output"
    event_file.write_text(json.dumps(payload))
    output.write_text("")
    result = subprocess.run(
        [sys.executable, "tools/ci/select_jobs.py", *args],
        cwd=root,
        env=env
        | {
            "GITHUB_EVENT_NAME": event,
            "GITHUB_EVENT_PATH": str(event_file),
            "GITHUB_OUTPUT": str(output),
        },
        capture_output=True,
        text=True,
    )
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    return result, {key: json.loads(value) for key, value in values.items()}


@pytest.mark.integration
def test_deleted_and_renamed_fixtures_use_merge_base_not_base_tip(
    selection_repo: tuple[Path, dict[str, str]],
) -> None:
    root, env = selection_repo
    assert cargo_directories(root) == ("buildins/echo/",)
    git(root, env, "branch", "base")
    git(root, env, "checkout", "-qb", "head")
    fixture = root / "buildins/echo/tests/inputs/data\nwith-newline"
    fixture.rename(root / "renamed.txt")
    git(root, env, "add", "-A")
    git(root, env, "commit", "-qm", "rename fixture")
    head = git(root, env, "rev-parse", "HEAD")
    git(root, env, "checkout", "base")
    (root / "infra").mkdir()
    (root / "infra/unrelated.py").write_text("")
    git(root, env, "add", "infra")
    git(root, env, "commit", "-qm", "unrelated base change")
    base = git(root, env, "rev-parse", "HEAD")
    git(root, env, "checkout", "head")
    result, values = run_selector(
        root, env, "pull_request", {"pull_request": {"base": {"sha": base}, "head": {"sha": head}}}
    )
    assert result.returncode == 0, result.stderr
    assert values["rust"] is True
    assert values["python"] is False
    assert values["languages"] == ["rust"]


@pytest.mark.integration
def test_missing_git_history_fails_without_success_outputs(
    selection_repo: tuple[Path, dict[str, str]],
) -> None:
    root, env = selection_repo
    result, values = run_selector(
        root,
        env,
        "pull_request",
        {"pull_request": {"base": {"sha": "missing"}, "head": {"sha": "HEAD"}}},
    )
    assert result.returncode != 0
    assert values == {}


@pytest.mark.integration
@pytest.mark.parametrize("event", ["schedule", "workflow_dispatch", "push"])
def test_full_runs_and_validation_push_smoke(
    selection_repo: tuple[Path, dict[str, str]], event: str
) -> None:
    root, env = selection_repo
    result, values = run_selector(root, env, event, {})
    assert result.returncode == 0, result.stderr
    assert all(values[name] is True for name in JOBS)
    assert values["languages"] == ["rust", "actions"]
    result, values = run_selector(root, env, event, {}, "--push-smoke")
    assert result.returncode == 0, result.stderr
    assert values["smoke"] is (event == "push")
    assert all(values[name] is (event != "push") for name in JOBS)


@pytest.mark.parametrize(
    ("selection", "results", "success"),
    [
        ("success", ["success", "skipped"], True),
        ("failure", ["skipped"], False),
        ("skipped", ["skipped"], False),
        ("success", ["failure"], False),
        ("success", ["cancelled"], False),
    ],
)
def test_required_gate_cannot_hide_selection_or_job_failures(
    selection: str, results: list[str], success: bool
) -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/pull-request-check.yml").read_text())
    command = workflow["jobs"]["validation"]["steps"][0]["run"]
    result = subprocess.run(
        ["bash", "-c", command],
        env=os.environ | {"SELECTION_RESULT": selection, "RESULTS": json.dumps(results)},
        capture_output=True,
    )
    assert (result.returncode == 0) is success
