"""Execute the workflow's native package lookup in an alternate layout."""

import os
import subprocess
from pathlib import Path

import yaml
from cli_check.workspace import find_root


def test_frontend_workflow_resolves_moved_packages(tmp_path: Path) -> None:
    workflow = yaml.load(
        (
            find_root(Path(__file__).resolve().parent) / ".github/workflows/pull-request-check.yml"
        ).read_text(),
        Loader=yaml.BaseLoader,
    )
    script = next(
        step["run"]
        for step in workflow["jobs"]["frontend"]["steps"]
        if step.get("id") == "projects"
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "0.0.0"\n[tool.uv]\npackage = false\n'
        'preview-features = ["workspace-dir"]\n[tool.uv.workspace]\nmembers = ["boxie", "apps/web"]\n'
    )
    for name, directory in (("boxie", "boxie"), ("cli-playground", "apps/web")):
        path = tmp_path / directory
        path.mkdir(parents=True)
        (path / "pyproject.toml").write_text(
            f'[project]\nname = "{name}"\nversion = "0.0.0"\n[tool.uv]\npackage = false\n'
        )
    output = tmp_path / "outputs"
    result = subprocess.run(
        ["bash", "-euc", script],
        cwd=tmp_path,
        env=os.environ | {"GITHUB_OUTPUT": str(output)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert f"boxie={tmp_path.resolve()}/boxie" in output.read_text()
    assert f"playground={tmp_path.resolve()}/apps/web" in output.read_text()


def test_lifecycle_allocation_requires_both_draft_policy_and_changed_inputs() -> None:
    workflow = yaml.load(
        (
            find_root(Path(__file__).resolve().parent) / ".github/workflows/boxie-lifecycle.yml"
        ).read_text(),
        Loader=yaml.BaseLoader,
    )
    job = workflow["jobs"]["sandbox"]
    assert job["needs"] == "select"
    assert (
        job["if"]
        == "${{ (!github.event.pull_request.draft || contains(github.event.pull_request.labels.*.name, 'run-ci')) && needs.select.outputs.changed == 'true' }}"
    )


def test_ordinary_ci_caches_dependencies_without_skipping_verification() -> None:
    root = find_root(Path(__file__).resolve().parent)
    for name in ("pull-request-check", "boxie-lifecycle", "cli-bench", "cli-playground"):
        workflow = yaml.load(
            (root / f".github/workflows/{name}.yml").read_text(), Loader=yaml.BaseLoader
        )
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                action = step.get("uses", "")
                if action.startswith("astral-sh/setup-uv@"):
                    assert step["with"]["enable-cache"] == "true"
                    assert step["with"]["cache-dependency-glob"].splitlines() == [
                        "uv.lock",
                        "**/pyproject.toml",
                    ]
                if action.startswith("voidzero-dev/setup-vp@"):
                    assert step["with"]["cache"] == "true"
                    assert (
                        step["with"]["cache-dependency-path"]
                        == "${{ steps.projects.outputs.playground }}/frontend/pnpm-lock.yaml"
                    )
                assert "cache-hit" not in step.get("if", "")
                assert "cache-hit" not in step.get("run", "")


def test_privileged_workflows_keep_caches_disabled() -> None:
    root = find_root(Path(__file__).resolve().parent)
    for name in (
        "cli-bench-report",
        "cli-playground-preview",
        "cli-playground-setup",
        "boxie-release",
        "cli-playground-release",
        "cli-playground-sandbox-release",
        "build-runtime-image",
    ):
        workflow = yaml.load(
            (root / f".github/workflows/{name}.yml").read_text(), Loader=yaml.BaseLoader
        )
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                action = step.get("uses", "")
                assert not action.startswith("Swatinem/rust-cache@")
                if action.startswith("astral-sh/setup-uv@"):
                    assert step["with"]["enable-cache"] == "false"
                if action.startswith("voidzero-dev/setup-vp@"):
                    assert step["with"]["cache"] == "false"


def test_rust_cache_families_preserve_compilation_and_platform_hashing() -> None:
    root = find_root(Path(__file__).resolve().parent)
    families = {
        "pull-request-check": {
            "quality": "rust-check-v1",
            "tests": "rust-test-v1",
            "packages": "rust-packages-v1",
        },
        "cli-bench": {"suites": "rust-release-v1"},
        "cli-playground": {"build": "rust-release-v1"},
    }
    for name, jobs in families.items():
        workflow = yaml.load(
            (root / f".github/workflows/{name}.yml").read_text(), Loader=yaml.BaseLoader
        )
        for job_id, key in jobs.items():
            steps = workflow["jobs"][job_id]["steps"]
            cache = next(
                step for step in steps if step.get("uses", "").startswith("Swatinem/rust-cache@")
            )
            assert len(cache["uses"].split("@")[1]) == 40
            assert cache["with"]["shared-key"] == key
            assert cache["with"]["cache-workspace-crates"] == "false"
            assert cache["with"]["add-rust-environment-hash-key"] == "true"
            later = steps[steps.index(cache) + 1 :]
            assert any(
                "cargo" in step.get("run", "")
                or "prek run cargo" in step.get("run", "")
                or 'preview_ci.py" build' in step.get("run", "")
                for step in later
            )


def test_generated_task_artifacts_live_outside_cached_cargo_targets() -> None:
    root = find_root(Path(__file__).resolve().parent)
    for name, job_id, directory in [
        ("cli-bench", "suites", "benchmarks"),
        ("cli-playground", "build", "playground-artifact"),
    ]:
        workflow = yaml.load(
            (root / f".github/workflows/{name}.yml").read_text(), Loader=yaml.BaseLoader
        )
        steps = workflow["jobs"][job_id]["steps"]
        upload = next(
            step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@")
        )
        assert upload["with"]["path"] == "${{ runner.temp }}/" + directory + "/"
