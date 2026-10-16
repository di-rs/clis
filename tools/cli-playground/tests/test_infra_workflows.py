"""Workflow contracts for privileged setup and ordinary runtime deployments."""

from pathlib import Path
from typing import Any

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


def test_only_manual_setup_can_provision_cloud_resources() -> None:
    setup = workflow("setup")
    runtime = workflow("release")
    assert set(setup["on"]) == {"workflow_dispatch"}
    provision = setup["jobs"]["provision"]
    deployment = runtime["jobs"]["deploy"]
    for job in (provision, deployment):
        assert job["environment"] == "playground"
        assert job["if"] == "${{ github.ref == 'refs/heads/master' }}"
        assert job["concurrency"] == {
            "group": "cli-playground-production",
            "cancel-in-progress": "false",
        }
    assert {"HCLOUD_TOKEN", "CLOUDFLARE_API_TOKEN"} <= set(provision["env"])
    assert not {"HCLOUD_TOKEN", "CLOUDFLARE_API_TOKEN"} & set(deployment["env"])
    provision_commands = "\n".join(step.get("run", "") for step in provision["steps"])
    runtime_commands = "\n".join(step.get("run", "") for step in deployment["steps"])
    assert "pulumi preview --refresh" in provision_commands
    assert "pulumi up --yes --refresh" in provision_commands
    assert "playground_infra.ci deploy" in runtime_commands
    assert "pulumi up" not in runtime_commands
    assert "pulumi preview" not in runtime_commands
    assert not (WORKFLOWS / "cli-playground-state.yml").exists()


def test_setup_only_provisions_and_release_is_explicitly_unavailable() -> None:
    setup = workflow("setup")
    release = workflow("release")
    assert set(setup["jobs"]) == {"provision"}
    assert release["jobs"]["deploy"]["needs"] == "release"
    first = release["jobs"]["release"]["steps"][0]
    assert "Production activation is unavailable" in first["run"]
    assert "exit 1" in first["run"]
    assert set(release["on"]["workflow_call"]["secrets"]) == DEPLOYMENT_SECRETS


def test_auto_release_is_opt_in_and_does_not_restart_runtime_for_ordinary_cli_changes() -> None:
    release = workflow("release")
    assert release["on"]["push"]["branches"] == ["master"]
    assert "vars.PLAYGROUND_AUTO_DEPLOY == 'true'" in release["jobs"]["release"]["if"]
    assert "github.event_name != 'push'" in release["jobs"]["release"]["if"]
    assert "workflow_dispatch" in release["on"]
    assert release["concurrency"] == {
        "group": "cli-playground-release",
        "cancel-in-progress": "false",
    }
    paths = release["on"]["push"]["paths"]
    assert "tools/cli-playground/src/playground/**" in paths
    assert "tools/cli-playground/frontend/**" in paths
    assert "tools/cli-playground/infra/compose.production.yml" in paths
    assert "tools/cli-playground/infra/host.py" not in paths
    assert not any(path.startswith(("coreutils/", "utils/", "docs/")) for path in paths)
    assert "tools/cli-playground/**" not in paths


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
