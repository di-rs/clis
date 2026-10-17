"""Workflow contracts for privileged setup and ordinary runtime deployments."""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[3] / ".github/workflows"
DEPLOYMENT_SECRETS = {
    "PLAYGROUND_DEPLOY_TOKEN",
    "PULUMI_CONFIG_PASSPHRASE",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "CLOUDFLARE_DNS_API_TOKEN",
}


def workflow(name: str) -> dict[str, Any]:
    # BaseLoader preserves the Actions 'on' key and expressions as strings.
    result: dict[str, Any] = yaml.load(
        (WORKFLOWS / f"cli-playground-{name}.yml").read_text(), Loader=yaml.BaseLoader
    )
    return result


@pytest.mark.parametrize("app", ["boxie", "playground"])
def test_application_release_uses_native_ssh_without_cloud_state(app: str) -> None:
    path = WORKFLOWS / ("boxie-release.yml" if app == "boxie" else "cli-playground-release.yml")
    release = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
    deployment = release["jobs"]["deploy"]
    assert deployment["environment"] == app
    assert "github.ref == 'refs/heads/master'" in deployment["if"]
    assert deployment["concurrency"] == {"group": "clis-production", "cancel-in-progress": "false"}
    text = str(deployment)
    for forbidden in (
        "PULUMI",
        "R2_",
        "AWS_",
        "HCLOUD",
        "CLOUDFLARE",
        "playground_infra.ci deploy",
    ):
        assert forbidden not in text
    commands = "\n".join(step.get("run", "") for step in deployment["steps"])
    for required in (
        "scp -F",
        "ssh -F",
        "StrictHostKeyChecking yes",
        "systemctl start",
        "-p Result",
        "-p SubState",
        "-p ExecMainStatus",
        "git merge-base --is-ancestor",
        "expected-revision",
    ):
        assert required in commands
    gate = release["jobs"]["release"]["if"]
    assert "DEPLOYMENT_ACCEPTANCE_PASSED" in gate
    assert "BOXIE_BACKUP_RESTORE_VERIFIED" in gate
    assert "AUTO_DEPLOY" in gate
    assert not any(
        path.startswith(("coreutils/", "utils/")) for path in release["on"]["push"]["paths"]
    )


def test_only_manual_setup_can_provision_cloud_resources() -> None:
    setup = workflow("setup")
    assert set(setup["on"]) == {"workflow_dispatch"}
    provision = setup["jobs"]["provision"]
    assert provision["environment"] == "platform"
    assert provision["concurrency"]["group"] == "clis-production"
    assert {"HCLOUD_TOKEN", "CLOUDFLARE_API_TOKEN"} <= set(provision["env"])
    assert any(
        step.get("working-directory") == "infra/environments/single-host"
        for step in provision["steps"]
    )


def test_preview_controller_keeps_privileged_code_on_the_default_branch() -> None:
    preview = workflow("preview")
    assert preview["on"]["workflow_run"] == {
        "workflows": ["🏗️ Playground PR binary builds"],
        "types": ["in_progress", "completed"],
    }
    assert preview["on"]["pull_request_target"] == {
        "branches": ["master"],
        "types": ["closed", "reopened"],
    }
    assert preview["permissions"] == {}
    assert set(preview["jobs"]) == {"publish", "lifecycle"}
    for job in preview["jobs"].values():
        checkout = job["steps"][0]
        assert checkout["with"] == {
            "ref": "${{ github.event.repository.default_branch }}",
            "persist-credentials": "false",
        }
        assert job["permissions"]["contents"] == "read"
        assert "CLOUDFLARE_DNS_API_TOKEN" not in str(job)
    assert "github.event_name == 'workflow_run'" in preview["jobs"]["publish"]["if"]
    assert "conclusion" not in preview["jobs"]["publish"]["if"]
    assert "github.event_name == 'pull_request_target'" in preview["jobs"]["lifecycle"]["if"]


def test_infrastructure_checks_require_trusted_dispatch_or_opted_in_schedule() -> None:
    path = WORKFLOWS / "cli-playground-infra-check.yml"
    assert path.exists(), "Protected infrastructure review workflow is missing"
    check = workflow("infra-check")
    assert set(check["on"]) == {"workflow_dispatch", "schedule"}
    selection = check["jobs"]["revision"]
    assert "github.ref == 'refs/heads/master'" in selection["if"]
    assert "vars.PLAYGROUND_INFRA_DRIFT_CHECK == 'true'" in selection["if"]
    assert "environment" not in selection
    assert "secrets." not in str(selection)
    protected = check["jobs"]["check"]
    assert protected["needs"] == "revision"
    assert protected["environment"] == "playground"
    assert protected["permissions"] == {"contents": "read"}
    assert protected["concurrency"]["group"] == "cli-playground-production"
    preview = next(step for step in protected["steps"] if step.get("id") == "preview")
    assert preview["with"]["command"] == "preview"
    assert preview["with"]["upsert"] == "false"
    assert preview["with"]["suppress-outputs"] == "true"
    assert preview["with"]["comment-on-summary"] == "true"
    assert preview["with"]["comment-on-pr"] == "false"


@pytest.mark.parametrize("requested", ["default", "head", "HEAD", "master", "0" * 40, "x\nrun=bad"])
def test_infrastructure_review_resolves_only_existing_full_commit_ids(
    tmp_path: Path, requested: str
) -> None:
    path = WORKFLOWS / "cli-playground-infra-check.yml"
    assert path.exists(), "Protected infrastructure review workflow is missing"
    selection = workflow("infra-check")["jobs"]["revision"]
    script = next(step["run"] for step in selection["steps"] if step.get("id") == "resolve")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    revision = "" if requested == "default" else head if requested == "head" else requested
    output = tmp_path / "output"
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        env={
            **os.environ,
            "REQUESTED_REVISION": revision,
            "GITHUB_SHA": head,
            "GITHUB_OUTPUT": str(output),
        },
        text=True,
        capture_output=True,
    )
    if requested in {"default", "head"}:
        assert result.returncode == 0, result.stderr
        assert output.read_text() == f"revision={head}\n"
    else:
        assert result.returncode != 0
        assert not output.exists()


@pytest.mark.parametrize("exit_code", [0, 2, 255])
def test_drift_check_only_previews_refresh_and_preserves_failure_status(
    tmp_path: Path, exit_code: int
) -> None:
    steps = workflow("infra-check")["jobs"]["check"]["steps"]
    script = next(step["run"] for step in steps if "Detect drift" in step.get("name", ""))
    calls = tmp_path / "calls"
    binary = tmp_path / "pulumi"
    binary.write_text(
        f"#!{sys.executable}\nimport pathlib, sys\n"
        f"with pathlib.Path({str(calls)!r}).open('a') as target:\n"
        "    target.write(' '.join(sys.argv[1:]) + '\\n')\n"
        f"sys.exit({exit_code} if sys.argv[1] == 'refresh' else 0)\n"
    )
    binary.chmod(0o755)
    summary = tmp_path / "summary"
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        env={
            **os.environ,
            "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
            "PULUMI_BACKEND_URL": "file:///review-state",
            "GITHUB_STEP_SUMMARY": str(summary),
        },
        text=True,
        capture_output=True,
    )
    assert result.returncode == exit_code, result.stderr
    assert calls.read_text().splitlines() == [
        "login file:///review-state --non-interactive",
        "stack select production --non-interactive",
        "refresh --preview-only --expect-no-changes --non-interactive --diff --suppress-outputs",
    ]
    assert (
        "No drift detected." if exit_code == 0 else "Drift check failed."
    ) in summary.read_text()


def test_delayed_release_refuses_to_copy_over_newer_success(tmp_path: Path) -> None:
    import json

    repository = tmp_path / "repo"
    repository.mkdir()
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}

    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(repository), *args], env=env, text=True
        ).strip()

    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    git("-c", "core.hooksPath=/dev/null", "commit", "--allow-empty", "-qm", "older")
    older = git("rev-parse", "HEAD")
    git("-c", "core.hooksPath=/dev/null", "commit", "--allow-empty", "-qm", "newer")
    newer = git("rev-parse", "HEAD")
    commands = tmp_path / "bin"
    commands.mkdir()
    log = tmp_path / "calls"
    for name in ("ssh", "scp"):
        executable = commands / name
        executable.write_text(
            f'#!{sys.executable}\nimport json, os, sys\nwith open(os.environ["CALLS"], "a") as f: f.write(json.dumps(sys.argv) + "\\n")\nprint(os.environ["CURRENT"])\n'
        )
        executable.chmod(0o755)
    script = workflow("release")["jobs"]["deploy"]["steps"][-1]["run"]
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", script],
        cwd=repository,
        env={
            **env,
            "PATH": str(commands) + ":" + env["PATH"],
            "CALLS": str(log),
            "CURRENT": newer,
            "GITHUB_SHA": older,
            "GITHUB_RUN_ID": "1",
            "GITHUB_RUN_ATTEMPT": "1",
            "DEPLOY_HOST": "example.com",
            "DEPLOY_SSH_KEY": "private",
            "DEPLOY_KNOWN_HOSTS": "known",
        },
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0
    assert "Refusing to replace a newer" in result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(calls) == 1
    assert "current/revision" in calls[0][-1]
