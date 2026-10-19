"""Run the release transport with fake SSH/SCP boundaries, without a host."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
IMAGE = "ghcr.io/test/new@sha256:" + "a" * 64
RETAINED = "ghcr.io/test/retained@sha256:" + "b" * 64


@pytest.fixture
def release_env(tmp_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    for name in subprocess.check_output(
        ["git", "rev-parse", "--local-env-vars"], text=True
    ).split():
        env.pop(name, None)
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, env=env, text=True
    ).strip()
    env.update(
        SOURCE_SHA=sha,
        BUILT_SHA=sha,
        IMAGE=IMAGE,
        DEPLOY_HOST="example.com",
        DEPLOY_SSH_KEY="private-key",
        DEPLOY_KNOWN_HOSTS="host-key",
        GHCR_TOKEN="private-token",
        GITHUB_ACTOR="test",
        GITHUB_RUN_ID="123",
        GITHUB_RUN_ATTEMPT="1",
        CALLS=str(tmp_path / "calls"),
        RETAINED=RETAINED,
        FAIL="",
        PATH=str(tmp_path) + os.pathsep + env["PATH"],
    )
    boundary = (
        f"#!{sys.executable}\n"
        + """import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
config = pathlib.Path(args[1])
record = {"name": name, "args": args, "private": str(config.parent)}
assert "StrictHostKeyChecking yes" in config.read_text()
assert all(p.stat().st_mode & 0o077 == 0 for p in config.parent.rglob("*") if p.is_file())
if name == "scp" and args[-1].endswith("/image.env"):
    record["image_env"] = pathlib.Path(args[-2]).read_text()
with open(os.environ["CALLS"], "a") as log:
    log.write(json.dumps(record) + "\\n")
if name == "ssh" and "sed -n" in args[-1]:
    print(os.environ["RETAINED"])
if os.environ["FAIL"] == "copy" and name == "scp":
    sys.exit(23)
if os.environ["FAIL"] == "deploy" and name == "ssh" and "DOCKER_CONFIG=" in args[-1]:
    sys.exit(24)
"""
    )
    for name in ("ssh", "scp"):
        path = tmp_path / name
        path.write_text(boundary)
        path.chmod(0o755)
    return env


def run_release(component: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(ROOT / "infra/environments/single-host/release.sh"), component],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
    )


@pytest.mark.parametrize(
    "component,owner,key,retained_key,command",
    [
        ("boxie", "boxie", "BOXIE_IMAGE", "OBS_COLLECTOR_IMAGE", "deploy.sh"),
        (
            "cli-playground-sandbox",
            "cli-playground",
            "CLI_PLAYGROUND_SANDBOX_IMAGE",
            "CLI_PLAYGROUND_BACKEND_IMAGE",
            "sandbox-release.sh",
        ),
        (
            "cli-playground-backend",
            "cli-playground",
            "CLI_PLAYGROUND_BACKEND_IMAGE",
            "CLI_PLAYGROUND_SANDBOX_IMAGE",
            "deploy.sh",
        ),
    ],
)
def test_release_preserves_other_image_and_cleans_credentials(
    release_env: dict[str, str],
    component: str,
    owner: str,
    key: str,
    retained_key: str,
    command: str,
) -> None:
    result = run_release(component, release_env)
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in Path(release_env["CALLS"]).read_text().splitlines()]
    image_env = next(call["image_env"] for call in calls if "image_env" in call)
    assert set(image_env.splitlines()) == {f"{key}={IMAGE}", f"{retained_key}={RETAINED}"}
    deployment = next(call["args"][-1] for call in calls if "DOCKER_CONFIG=" in call["args"][-1])
    assert f"/opt/sanctuary/{owner}/{command}" in deployment
    assert f"/etc/sanctuary/{owner}-image.env" in deployment
    assert "install -m 0600" in deployment
    assert (
        IMAGE
        if component == "cli-playground-sandbox"
        else release_env["SOURCE_SHA"]
        if component == "boxie"
        else "deploy.sh"
    ) in deployment
    assert calls[-1]["args"][-1].startswith("rm -rf /run/sanctuary-")
    assert not Path(calls[0]["private"]).exists()
    assert "private-key" not in result.stdout + result.stderr
    assert "private-token" not in result.stdout + result.stderr


@pytest.mark.parametrize("failure,code", [("copy", 23), ("deploy", 24)])
def test_release_failure_cleans_credentials_and_preserves_exit_status(
    release_env: dict[str, str], failure: str, code: int
) -> None:
    release_env["FAIL"] = failure
    result = run_release("boxie", release_env)
    assert result.returncode == code
    calls = [json.loads(line) for line in Path(release_env["CALLS"]).read_text().splitlines()]
    assert calls[-1]["args"][-1].startswith("rm -rf /run/sanctuary-")
    assert not Path(calls[0]["private"]).exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("IMAGE", "image:latest"),
        ("BUILT_SHA", "0" * 40),
        ("SOURCE_SHA", "0" * 40),
        ("DEPLOY_HOST", "host;touch /tmp/bad"),
        ("GITHUB_RUN_ID", "1;false"),
    ],
)
def test_release_rejects_invalid_inputs_before_ssh(
    release_env: dict[str, str], field: str, value: str
) -> None:
    release_env[field] = value
    assert run_release("boxie", release_env).returncode != 0
    assert not Path(release_env["CALLS"]).exists()


def test_release_rejects_unknown_component(release_env: dict[str, str]) -> None:
    assert run_release("unknown", release_env).returncode != 0
    assert not Path(release_env["CALLS"]).exists()
