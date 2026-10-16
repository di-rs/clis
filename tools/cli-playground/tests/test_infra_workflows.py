"""Trust and credential boundaries of the executable Actions configuration."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

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


def test_setup_delivers_tunnel_secret_through_stdin(tmp_path: Path) -> None:
    script = workflow("cli-playground-setup.yml")["jobs"]["provision"]["steps"][-1]["run"]
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
        "workflows": ["CLI playground build"],
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
