"""Execute the workflow's native package lookup in an alternate layout."""

import os
import subprocess
from pathlib import Path

import yaml
from cli_check.workspace import find_root


def test_frontend_workflow_resolves_moved_packages(tmp_path: Path) -> None:
    workflow = yaml.load(
        (find_root(Path.cwd()) / ".github/workflows/pull-request-check.yml").read_text(),
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
        (find_root(Path.cwd()) / ".github/workflows/boxie-lifecycle.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    job = workflow["jobs"]["sandbox"]
    assert job["needs"] == "select"
    assert (
        job["if"]
        == "${{ (!github.event.pull_request.draft || contains(github.event.pull_request.labels.*.name, 'run-ci')) && needs.select.outputs.changed == 'true' }}"
    )
