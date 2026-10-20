"""Exercise the manual reset workflow without cloud access."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[4]
PREFIX = "urn:pulumi:production::sanctuary::"
SERVER = PREFIX + "hcloud:index/server:Server::sanctuary"
VOLUME = PREFIX + "hcloud:index/volume:Volume::sanctuary-data"


def workflow(name: str) -> dict[str, Any]:
    return dict(
        yaml.load(
            (ROOT / f".github/workflows/sanctuary-{name}.yml").read_text(), Loader=yaml.BaseLoader
        )
    )


@pytest.fixture
def boundaries(tmp_path: Path) -> dict[str, str]:
    env = {
        **os.environ,
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "CALLS": str(tmp_path / "calls"),
        "FAIL": "",
        "URNS": json.dumps([SERVER, VOLUME]),
        "DELETED": "[]",
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        "PULUMI_BACKEND_URL": "s3://existing-state",
    }
    script = (
        f"#!{sys.executable}\n"
        + """import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
record = {"name": name, "args": args}
with open(os.environ["CALLS"], "a") as log:
    log.write(json.dumps(record) + "\\n")
if os.environ["FAIL"] in (name, name + ":" + args[0]):
    sys.exit(17)
if name == "pulumi" and args == ["stack", "export"]:
    calls = [json.loads(line) for line in pathlib.Path(os.environ["CALLS"]).read_text().splitlines()]
    refreshed = any(call["args"][0] == "refresh" for call in calls)
    deleted = json.loads(os.environ["DELETED"]) if refreshed else []
    resources = [{"urn": urn} for urn in json.loads(os.environ["URNS"]) if urn not in deleted]
    print(json.dumps({"deployment": {"resources": resources}}))
"""
    )
    path = tmp_path / "pulumi"
    path.write_text(script)
    path.chmod(0o755)
    return env


def calls(env: dict[str, str]) -> list[dict[str, Any]]:
    path = Path(env["CALLS"])
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.mark.parametrize(
    "confirmation,acceptance,drift,allowed",
    [
        ("destroy-production", "false", "false", True),
        ("destroy-production", "false", "", True),
        ("", "false", "false", False),
        ("production", "false", "false", False),
        ("destroy-production", "true", "false", False),
        ("destroy-production", "", "false", False),
        ("destroy-production", "false", "true", False),
    ],
)
def test_reset_requires_explicit_intent_and_disabled_automation(
    confirmation: str,
    acceptance: str,
    drift: str,
    allowed: bool,
) -> None:
    definition = workflow("reset")
    assert set(definition["on"]) == {"workflow_dispatch"}
    assert definition["concurrency"] == workflow("setup")["concurrency"]
    job = definition["jobs"]["reset"]
    assert job["environment"] == "sanctuary"
    assert job["if"] == "${{ github.ref == 'refs/heads/master' }}"
    result = subprocess.run(
        ["bash", "-c", job["steps"][0]["run"]],
        env={
            **os.environ,
            "CONFIRMATION": confirmation,
            "ACCEPTANCE": acceptance,
            "DRIFT_CHECK": drift,
        },
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) == allowed


@pytest.mark.parametrize(
    "recorded,deleted",
    [([SERVER, VOLUME], []), ([VOLUME], []), ([], []), ([SERVER, VOLUME], [SERVER])],
)
def test_reset_targets_only_remaining_resources_and_keeps_stack(
    boundaries: dict[str, str],
    recorded: list[str],
    deleted: list[str],
) -> None:
    boundaries["URNS"] = json.dumps(recorded)
    boundaries["DELETED"] = json.dumps(deleted)
    remaining = set(recorded) - set(deleted)
    result = subprocess.run(
        ["bash", "-c", workflow("reset")["jobs"]["reset"]["steps"][-1]["run"]],
        env=boundaries,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    commands = [record["args"] for record in calls(boundaries)]
    assert ["stack", "select", "production", "--non-interactive"] in commands
    assert commands.index(
        ["refresh", "--yes", "--non-interactive", "--suppress-outputs"]
    ) < commands.index(["stack", "export"])
    assert not any("--create" in command or command[:2] == ["stack", "rm"] for command in commands)
    updates = [command for command in commands if command[0] == "up"]
    assert len(updates) == bool(remaining)
    if updates:
        assert set(updates[0][5::2]) == remaining
        assert commands.index(updates[0]) < next(
            i for i, cmd in enumerate(commands) if cmd[0] == "destroy"
        )
    assert commands[-1] == ["config", "set", "sanctuary:protect", "true", "--non-interactive"]


@pytest.mark.parametrize("failure", ["pulumi:stack", "pulumi:refresh", "pulumi:up"])
def test_reset_does_not_destroy_after_a_failed_preparation(
    boundaries: dict[str, str], failure: str
) -> None:
    boundaries["FAIL"] = failure
    result = subprocess.run(
        ["bash", "-c", workflow("reset")["jobs"]["reset"]["steps"][-1]["run"]],
        env=boundaries,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert not any(record["args"][0] == "destroy" for record in calls(boundaries))
