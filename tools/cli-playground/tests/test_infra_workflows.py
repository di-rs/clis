"""Workflow contracts for privileged setup and ordinary Compose releases."""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from cli_check.workspace import find_root

WORKFLOWS = find_root(Path(__file__).resolve().parent) / ".github/workflows"
QUEUE = {"group": "clis-production", "queue": "max", "cancel-in-progress": "false"}


def workflow(name: str) -> dict[str, Any]:
    result: dict[str, Any] = yaml.load(
        (WORKFLOWS / f"cli-playground-{name}.yml").read_text(), Loader=yaml.BaseLoader
    )
    return result


@pytest.mark.parametrize(
    "filename,job_name",
    [
        ("boxie-release.yml", "deploy"),
        ("cli-playground-release.yml", "deploy"),
        ("cli-playground-sandbox-release.yml", "deploy"),
        ("cli-playground-setup.yml", "provision"),
    ],
)
@pytest.mark.parametrize(
    "source_attempt,current_attempt,allowed",
    [("2", "2", True), ("1", "2", False), ("", "2", False)],
)
def test_deployment_rejects_partial_workflow_retry(
    filename: str, job_name: str, source_attempt: str, current_attempt: str, allowed: bool
) -> None:
    definition = yaml.load((WORKFLOWS / filename).read_text(), Loader=yaml.BaseLoader)
    source = definition["jobs"]["source"]
    assert source["outputs"]["attempt"] == "${{ steps.revision.outputs.attempt }}"
    assert any("GITHUB_RUN_ATTEMPT" in step.get("run", "") for step in source["steps"])
    guarded = definition["jobs"][job_name]["steps"][0]
    assert guarded["env"]["SOURCE_ATTEMPT"] == "${{ needs.source.outputs.attempt }}"
    result = subprocess.run(
        ["bash", "-euo", "pipefail", "-c", guarded["run"]],
        env={**os.environ, "SOURCE_ATTEMPT": source_attempt, "GITHUB_RUN_ATTEMPT": current_attempt},
        text=True,
        capture_output=True,
    )
    assert (result.returncode == 0) == allowed
    if not allowed:
        assert "Re-run all jobs" in result.stderr


def test_setup_queues_before_selecting_master_and_builds_all_images() -> None:
    setup = workflow("setup")
    assert setup["on"]["workflow_dispatch"] == {}
    assert setup["concurrency"] == QUEUE
    jobs = setup["jobs"]
    assert jobs["source"]["steps"][0]["with"]["ref"] == "master"
    for name, component in {
        "boxie": "boxie",
        "gateway": "playground-gateway",
        "sandbox": "playground-sandbox",
        "collector": "collector",
    }.items():
        job = jobs[name]
        assert job["uses"] == "./.github/workflows/build-runtime-image.yml"
        assert job["needs"] == "source"
        assert job["with"] == {
            "component": component,
            "revision": "${{ needs.source.outputs.sha }}",
        }
    assert set(jobs["provision"]["needs"]) == {"source", "boxie", "gateway", "sandbox", "collector"}
    assert "concurrency" not in jobs["provision"]


def test_reusable_builder_uses_fixed_components_without_deployment_secrets() -> None:
    builder = yaml.load((WORKFLOWS / "build-runtime-image.yml").read_text(), Loader=yaml.BaseLoader)
    job = builder["jobs"]["build"]
    assert set(builder["on"]) == {"workflow_call"}
    assert job["permissions"] == {"contents": "read", "packages": "write"}
    assert "Runtime.Dockerfile" in str(job)
    assert "Playground.Dockerfile" in str(job)
    assert "Sandbox.Dockerfile" in str(job)
    assert "observability/collector" in str(job)
    assert "secrets." not in str(job)


@pytest.mark.parametrize(
    "filename,app",
    [
        ("boxie-release.yml", "boxie"),
        ("cli-playground-release.yml", "playground"),
        ("cli-playground-sandbox-release.yml", "playground"),
    ],
)
def test_release_uses_one_queue_current_master_and_direct_ssh(filename: str, app: str) -> None:
    release = yaml.load((WORKFLOWS / filename).read_text(), Loader=yaml.BaseLoader)
    assert release["concurrency"] == QUEUE
    assert release["jobs"]["source"]["steps"][0]["with"]["ref"] == "master"
    deployment = release["jobs"]["deploy"]
    assert deployment["environment"] == app
    assert "concurrency" not in deployment
    gate = deployment["if"]
    assert "DEPLOYMENT_ACCEPTANCE_PASSED" in gate
    assert "BOXIE_BACKUP_RESTORE_VERIFIED" in gate
    assert "AUTO_DEPLOY" in gate
    commands = "\n".join(step.get("run", "") for step in deployment["steps"])
    assert "ssh -F" in commands
    assert "scp -F" in commands
    assert "submit-release.sh" not in commands
    assert "release_selection.py" not in commands
    assert "expected-revision" not in commands
    assert "PULUMI_" not in str(deployment)
    assert "R2_" not in str(deployment)
    assert "HCLOUD" not in str(deployment)
    assert "paths" not in release["on"]["push"]
    assert release["jobs"]["build"]["if"] == "${{ needs.source.outputs.changed == 'true' }}"


def test_gateway_release_does_not_build_sandbox() -> None:
    gateway = workflow("release")
    assert "sandbox" not in gateway["jobs"]
    assert "Sandbox.Dockerfile*" not in str(
        gateway["jobs"]["source"]["steps"][-1]["env"]["OWNER_INPUTS"]
    )
    sandbox = workflow("sandbox-release")
    assert "Sandbox.Dockerfile*" in str(
        sandbox["jobs"]["source"]["steps"][-1]["env"]["OWNER_INPUTS"]
    )
    assert "fixtures/" in str(sandbox["jobs"]["source"]["steps"][-1]["env"]["OWNER_INPUTS"])


def test_only_manual_setup_can_provision_cloud_resources() -> None:
    setup = workflow("setup")
    assert set(setup["on"]) == {"workflow_dispatch"}
    provision = setup["jobs"]["provision"]
    assert provision["environment"] == "platform"
    assert {"HCLOUD_TOKEN", "CLOUDFLARE_API_TOKEN"} <= set(provision["env"])
    assert any(
        step.get("working-directory") == "infra/environments/single-host"
        for step in provision["steps"]
    )
    commands = "\n".join(step.get("run", "") for step in provision["steps"])
    assert commands.index("pulumi up") < commands.index("render_inputs")
    assert "cloud-init status --wait" in commands
    assert "install.sh" in commands
    assert "submit-release.sh" not in commands


def test_setup_pulls_all_images_before_starting_boxie() -> None:
    installer = (WORKFLOWS.parent.parent / "infra/environments/single-host/install.sh").read_text()
    assert "PLAYGROUND_SANDBOX_IMAGE" in installer
    assert installer.index('docker pull "$image"') < installer.index(
        "bash /opt/clis/boxie/install.sh"
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
        assert job["environment"] == "playground-preview"
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
    assert protected["environment"] == "platform"
    assert protected["permissions"] == {"contents": "read"}
    assert check["concurrency"] == {
        "group": "clis-production",
        "queue": "max",
        "cancel-in-progress": "false",
    }
    preview = next(step for step in protected["steps"] if step.get("id") == "preview")
    assert "concurrency" not in protected
    assert preview["with"]["command"] == "preview"
    assert preview["with"]["upsert"] == "false"
    assert preview["with"]["suppress-outputs"] == "true"
    assert preview["with"]["comment-on-summary"] == "true"
    assert preview["with"]["comment-on-pr"] == "false"


def test_infrastructure_review_does_not_stage_application_installation() -> None:
    steps = workflow("infra-check")["jobs"]["check"]["steps"]
    commands = "\n".join(step.get("run", "") for step in steps)
    assert ".installation" not in commands
    assert "sourceDirectory" not in commands


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


def test_legacy_ssh_transport_has_no_remaining_entrypoints() -> None:
    root = WORKFLOWS.parent.parent
    for path in (
        "tools/cli-playground/infra/playground_infra/deploy.py",
        "tools/cli-playground/infra/playground_infra/ci.py",
        "infra/environments/single-host/configure-github.sh",
    ):
        assert not (root / path).exists()
