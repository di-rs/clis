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
}


def workflow(name: str) -> dict[str, Any]:
    # BaseLoader preserves the Actions 'on' key and expressions as strings.
    result: dict[str, Any] = yaml.load(
        (WORKFLOWS / f"cli-playground-{name}.yml").read_text(), Loader=yaml.BaseLoader
    )
    return result


def test_only_manual_setup_can_provision_cloud_resources() -> None:
    setup = workflow("setup")
    runtime = workflow("deploy")
    assert set(setup["on"]) == {"workflow_dispatch"}
    assert set(runtime["on"]) == {"workflow_call"}
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
    assert "ci.py deploy" in runtime_commands
    assert "pulumi up" not in runtime_commands
    assert "pulumi preview" not in runtime_commands
    assert not (WORKFLOWS / "cli-playground-state.yml").exists()


def test_initial_setup_and_releases_share_immutable_deployment_and_secret_forwarding() -> None:
    setup = workflow("setup")
    release = workflow("release")
    deployment = workflow("deploy")
    assert setup["jobs"]["release"]["uses"] == "./.github/workflows/cli-playground-release.yml"
    assert release["jobs"]["deploy"]["uses"] == "./.github/workflows/cli-playground-deploy.yml"
    assert setup["jobs"]["release"]["needs"] == "provision"
    assert release["jobs"]["deploy"]["needs"] == "release"
    for caller in (setup["jobs"]["release"], release["jobs"]["deploy"]):
        assert caller["secrets"] == {
            name: "${{ secrets." + name + " }}" for name in DEPLOYMENT_SECRETS
        }
    for called in (release, deployment):
        assert set(called["on"]["workflow_call"]["secrets"]) == DEPLOYMENT_SECRETS
        assert all(
            secret["required"] == "true"
            for secret in called["on"]["workflow_call"]["secrets"].values()
        )
    assert release["jobs"]["deploy"]["with"] == {
        key: "${{ needs.release.outputs." + key + " }}"
        for key in ("revision", "gateway_image", "sandbox_image")
    }
    outputs = release["jobs"]["release"]["outputs"]
    assert outputs["revision"] == "${{ github.sha }}"
    assert outputs["gateway_image"].endswith("@${{ steps.gateway.outputs.digest }}")
    assert outputs["sandbox_image"].endswith("@${{ steps.sandbox.outputs.digest }}")


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
    assert "tools/cli-playground/playground/**" in paths
    assert "tools/cli-playground/frontend/**" in paths
    assert "tools/cli-playground/compose.production.yml" in paths
    assert "tools/cli-playground/infra/host.py" not in paths
    assert not any(path.startswith(("coreutils/", "utils/", "docs/")) for path in paths)
    assert "tools/cli-playground/**" not in paths
