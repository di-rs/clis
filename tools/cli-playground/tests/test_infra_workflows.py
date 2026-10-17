"""Trust and credential boundaries of the executable Actions configuration."""

import json
import os
import subprocess
import sys
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[3] / ".github/workflows"


def workflow(name: str) -> dict[str, Any]:
    return yaml.load((WORKFLOWS / name).read_text(), Loader=yaml.BaseLoader)  # type: ignore[no-any-return]


def test_only_manual_platform_setup_has_provisioning_credentials() -> None:
    setup = workflow("cli-playground-setup.yml")
    runtime = workflow("component-release.yml")
    assert set(setup["on"]) == {"workflow_dispatch"}
    provision = setup["jobs"]["provision"]
    assert provision["environment"] == "platform"
    assert provision["if"] == "${{ github.ref == 'refs/heads/master' }}"
    assert {"HCLOUD_TOKEN", "CLOUDFLARE_API_TOKEN", "PULUMI_CONFIG_PASSPHRASE"} <= set(
        provision["env"]
    )
    forbidden = ("PULUMI_", "HCLOUD_", "R2_", "AWS_", "CLOUDFLARE_API_TOKEN")
    for job in runtime["jobs"].values():
        assert not any(key.startswith(forbidden) for key in job.get("env", {}))
        for step in job["steps"]:
            assert not any(key.startswith(forbidden) for key in step.get("env", {}))
    deployment = runtime["jobs"]["deploy"]
    assert deployment["timeout-minutes"] == "20"
    assert deployment["environment"] == "${{ inputs.component }}"
    assert deployment["concurrency"]["cancel-in-progress"] == "false"
    assert deployment["permissions"] == {"contents": "read"}


def test_independent_release_callers_select_only_their_component() -> None:
    for component, filename, variable in [
        ("boxie", "boxie-release.yml", "BOXIE_RUNTIME_IMAGE"),
        ("playground", "cli-playground-release.yml", "PLAYGROUND_IMAGE"),
    ]:
        release = workflow(filename)
        assert release["on"]["push"]["branches"] == ["master"]
        assert "workflow_dispatch" in release["on"]
        assert not any(
            path.startswith(("coreutils/", "utils/")) for path in release["on"]["push"]["paths"]
        )
        application = release["jobs"]["application"]
        assert application["uses"] == "./.github/workflows/component-release.yml"
        assert application["with"]["component"] == component
        assert application["with"]["image_variable"] == variable
        assert "github.ref == 'refs/heads/master'" in application["if"]
        assert component.upper() + "_AUTO_DEPLOY == 'true'" in application["if"]


@pytest.mark.parametrize(
    "filename,job",
    [
        ("cli-playground-setup.yml", "provision"),
        ("cli-playground-infra-check.yml", "check"),
    ],
)
def test_platform_commands_deliver_tunnel_secret_through_stdin(
    tmp_path: Path, filename: str, job: str
) -> None:
    steps = workflow(filename)["jobs"][job]["steps"]
    script = next(
        step["run"]
        for step in steps
        if "pulumi login" in step.get("run", "") and "config set" in step["run"]
    )
    assert (
        workflow(filename)["jobs"][job]["env"]["OPERATOR_TUNNEL_SECRET"]
        == "${{ secrets.OPERATOR_TUNNEL_SECRET }}"
    )
    peer = tmp_path / "pulumi"
    record = tmp_path / "calls"
    peer.write_text(
        f"#!{sys.executable}\nimport json,os,sys\n"
        "args=sys.argv[1:]\n"
        "value=sys.stdin.read() if args[:2]==['config','set'] else ''\n"
        "with open(os.environ['CALLS'],'a') as f: f.write(json.dumps([args,value])+'\\n')\n"
    )
    peer.chmod(0o700)
    result = subprocess.run(
        ["bash", "-euc", script],
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        env={
            **os.environ,
            "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
            "CALLS": str(record),
            "PULUMI_BACKEND_URL": "s3://state",
            "OPERATOR_TUNNEL_SECRET": "synthetic-tunnel-secret",
        },
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in record.read_text().splitlines()]
    assert all("synthetic-tunnel-secret" not in " ".join(args) for args, _ in calls)
    assert [value for args, value in calls if args[:2] == ["config", "set"]] == [
        "synthetic-tunnel-secret"
    ]


def test_preview_controller_keeps_privileged_code_on_the_default_branch() -> None:
    preview = workflow("cli-playground-preview.yml")
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
        assert job["steps"][0]["with"] == {
            "ref": "${{ github.event.repository.default_branch }}",
            "persist-credentials": "false",
        }
        assert job["permissions"]["contents"] == "read"
        assert "CLOUDFLARE_DNS_API_TOKEN" not in str(job)


def test_infrastructure_checks_require_trusted_dispatch_or_opted_in_schedule() -> None:
    path = WORKFLOWS / "cli-playground-infra-check.yml"
    assert path.exists(), "Protected infrastructure review workflow is missing"
    check = workflow("cli-playground-infra-check.yml")
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
    assert protected["concurrency"]["group"] == "platform-production"
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
    selection = workflow("cli-playground-infra-check.yml")["jobs"]["revision"]
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
    steps = workflow("cli-playground-infra-check.yml")["jobs"]["check"]["steps"]
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


def test_migration_changes_select_the_boxie_runtime_release() -> None:
    paths = workflow("boxie-release.yml")["on"]["push"]["paths"]
    for changed in ("tools/boxie/alembic/versions/0005_example.py", "tools/boxie/alembic.ini"):
        assert any(fnmatchcase(changed, pattern) for pattern in paths)
