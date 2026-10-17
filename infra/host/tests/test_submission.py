"""The shared transport accepts only fixed release operations and pinned SSH."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "infra/host/submit-release.sh"
REVISION = "a" * 40
IMAGE = "ghcr.io/example/boxie@sha256:" + "b" * 64


def submission(
    tmp_path: Path,
    app: str = "boxie",
    *,
    image: str = IMAGE,
    run_id: str = "123",
    fail_start: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, Any]]]:
    binary = tmp_path / "bin"
    binary.mkdir()
    log = tmp_path / "calls"
    fake = f"""#!{sys.executable}
import json, os, pathlib, sys
config = pathlib.Path(sys.argv[sys.argv.index('-F') + 1]).read_text()
with open(os.environ['CALLS'], 'a') as target:
    target.write(json.dumps({{'program': pathlib.Path(sys.argv[0]).name, 'args': sys.argv[1:], 'config': config}}) + '\\n')
if os.environ.get('FAIL_START') and 'systemctl start' in ' '.join(sys.argv):
    sys.exit(255)
if 'current/revision' in ' '.join(sys.argv):
    print(os.environ['GITHUB_SHA'])
"""
    for name in ("ssh", "scp"):
        path = binary / name
        path.write_text(fake)
        path.chmod(0o755)
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "revision").write_text(REVISION + "\n")
    (payload / "expected-revision").write_text("c" * 40 + "\n")
    (payload / "image.env").write_text("BOXIE_RUNTIME_IMAGE=" + image + "\n")
    result = subprocess.run(
        ["bash", str(SCRIPT), app, str(payload)],
        env={
            **os.environ,
            "PATH": str(binary) + os.pathsep + os.environ["PATH"],
            "CALLS": str(log),
            "DEPLOY_HOST": "example.com",
            "DEPLOY_SSH_KEY": "private-test-key",
            "DEPLOY_KNOWN_HOSTS": "example.com ssh-ed25519 YWJj",
            "GITHUB_RUN_ID": run_id,
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_SHA": REVISION,
            "FAIL_START": "1" if fail_start else "",
        },
        text=True,
        capture_output=True,
    )
    return result, [
        json.loads(line) for line in log.read_text().splitlines()
    ] if log.exists() else []


def test_submission_pins_host_key_and_verifies_success(tmp_path: Path) -> None:
    result, calls = submission(tmp_path)
    assert result.returncode == 0, result.stderr
    assert any(call["program"] == "scp" for call in calls)
    assert any("systemctl start clis-boxie@123-1.service" in str(call["args"]) for call in calls)
    assert any("current/revision" in str(call["args"]) for call in calls)
    assert all("StrictHostKeyChecking yes" in call["config"] for call in calls)
    assert all("UserKnownHostsFile" in call["config"] for call in calls)
    assert all(not Path(call["args"][call["args"].index("-F") + 1]).exists() for call in calls)
    assert "private-test-key" not in result.stdout + result.stderr


@pytest.mark.parametrize("app", ["bad", "boxie;id", "setup/other"])
def test_submission_rejects_invalid_application_before_transport(tmp_path: Path, app: str) -> None:
    result, calls = submission(tmp_path, app)
    assert result.returncode != 0
    assert calls == []


def test_submission_rejects_malformed_image_before_transport(tmp_path: Path) -> None:
    result, calls = submission(tmp_path, image="latest")
    assert result.returncode != 0
    assert calls == []


def test_submission_rejects_invalid_run_before_transport(tmp_path: Path) -> None:
    result, calls = submission(tmp_path, run_id="1;id")
    assert result.returncode != 0
    assert calls == []


def test_disconnected_start_reports_unknown_outcome(tmp_path: Path) -> None:
    result, calls = submission(tmp_path, fail_start=True)
    assert result.returncode != 0
    assert "requires inspection" in result.stderr
    assert any("systemctl start" in str(call["args"]) for call in calls)
