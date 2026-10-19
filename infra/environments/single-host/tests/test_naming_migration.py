"""The offline cutover keeps originals and refuses conflicting or unsafe copies."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "infra/environments/single-host/migrate-names.py"


def migrate(source: Path, target: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(source), str(target)], capture_output=True, text=True
    )


@pytest.fixture
def old_config(tmp_path: Path) -> Path:
    source = tmp_path / "old"
    source.mkdir(mode=0o700)
    (source / "boxie").mkdir(mode=0o700)
    (source / "playground.env").write_text(
        "PLAYGROUND_IMAGE='ghcr.io/org/playground-gateway@sha256:abc'\n"
        "PLAYGROUND_DEPLOY_TOKEN='playground-orchestrator-opaque-token'\n"
        "PLAYGROUND_DATA_VOLUME='playground-data'\n"
        "PLAYGROUND_PROJECT_NAME='playground'\n"
    )
    (source / "prometheus.yml").write_text(
        "- job_name: boxie-orchestrator-sdk\n  - targets:\n    - boxie-orchestrator:9203\n"
        "    regex: boxie/id/boxie-orchestrator/lifecycle\n"
    )
    (source / "boxie/orchestrator.secret").write_bytes(b"orchestrator-secret\x00\xff\n")
    (source / "boxie/api.json").write_text(
        json.dumps(
            {
                "orchestrator_url": "http://orchestrator:8091",
                "consumer_credential_files": {"playground": "/run/secrets/consumer"},
                "identity": {"host_id": "playground", "installation_id": "keep-id"},
            }
        )
    )
    (source / "boxie/profiles.json").write_text(
        json.dumps(
            {
                "playground": {
                    "commands": [["/opt/playground/shell.sh"], ["/custom/shell", "playground"]],
                    "env": {"orchestrator_mode": ["/opt/playground/shell.sh", "playground"]},
                    "image_digest": "old-digest",
                    "custom": 42,
                }
            }
        )
    )
    (source / "playground.env").chmod(0o600)
    (source / "boxie/orchestrator.secret").chmod(0o600)
    return source


def test_configuration_copy_preserves_identity_secrets_and_originals(old_config: Path) -> None:
    target = old_config.parent / "new"
    before = {
        p.relative_to(old_config): p.read_bytes() for p in old_config.rglob("*") if p.is_file()
    }
    result = migrate(old_config, target)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert {
        p.relative_to(old_config): p.read_bytes() for p in old_config.rglob("*") if p.is_file()
    } == before
    assert (target / "cli-playground.env").read_text() == (
        "CLI_PLAYGROUND_BACKEND_IMAGE='ghcr.io/org/playground-gateway@sha256:abc'\n"
        "CLI_PLAYGROUND_DEPLOY_TOKEN='playground-orchestrator-opaque-token'\n"
        "CLI_PLAYGROUND_DATA_VOLUME='cli-playground-data'\n"
        "CLI_PLAYGROUND_PROJECT_NAME='cli-playground'\n"
    )
    assert (target / "prometheus.yml").read_text() == (
        "- job_name: boxie-supervisor-sdk\n  - targets:\n    - boxie-supervisor:9203\n"
        "    regex: boxie/id/boxie-orchestrator/lifecycle\n"
    )
    secret = target / "boxie/supervisor.secret"
    assert secret.read_bytes() == before[Path("boxie/orchestrator.secret")]
    assert secret.stat().st_mode & 0o077 == 0
    assert (secret.stat().st_uid, secret.stat().st_gid) == (os.getuid(), os.getgid())
    api = json.loads((target / "boxie/api.json").read_text())
    assert api["supervisor_url"] == "http://supervisor:8091"
    assert api["consumer_credential_files"] == {"cli-playground": "/run/secrets/consumer"}
    assert api["identity"] == {"host_id": "playground", "installation_id": "keep-id"}
    assert json.loads((target / "boxie/profiles.json").read_text()) == {
        "cli-playground": {
            "commands": [["/opt/cli-playground/shell.sh"], ["/custom/shell", "playground"]],
            "env": {"orchestrator_mode": ["/opt/playground/shell.sh", "playground"]},
            "image_digest": "old-digest",
            "custom": 42,
        }
    }
    assert migrate(old_config, target).returncode != 0
    assert secret.read_bytes() == before[Path("boxie/orchestrator.secret")]


@pytest.mark.parametrize("conflict", ["env", "json", "file", "permissions", "symlink"])
def test_failed_copy_leaves_no_target_and_can_retry(old_config: Path, conflict: str) -> None:
    target = old_config.parent / "new"
    if conflict == "env":
        file = old_config / "playground.env"
        original = file.read_bytes()
        file.write_bytes(original + b"CLI_PLAYGROUND_DATA_VOLUME='other'\n")
    elif conflict == "json":
        file = old_config / "boxie/profiles.json"
        original = file.read_bytes()
        file.write_text('{"playground":{},"cli-playground":{}}')
    elif conflict == "file":
        file = old_config / "boxie/supervisor.secret"
        original = None
        file.write_text("conflicting-secret")
        file.chmod(0o600)
    elif conflict == "permissions":
        file = old_config / "boxie/orchestrator.secret"
        original = file.read_bytes()
        file.chmod(0o644)
    else:
        file = old_config / "linked-secret"
        original = None
        file.symlink_to(old_config / "boxie/orchestrator.secret")
    assert migrate(old_config, target).returncode != 0
    assert not target.exists()
    assert not list(old_config.parent.glob(".new.*"))
    if original is None:
        file.unlink()
    else:
        file.write_bytes(original)
        file.chmod(0o600)
    assert migrate(old_config, target).returncode == 0


@pytest.mark.parametrize("failure", ["", "running", "target", "verification"])
def test_volume_copy_checks_consumers_and_verifies_before_success(
    tmp_path: Path, failure: str
) -> None:
    boundary = tmp_path / "bin"
    boundary.mkdir()
    for name in ("old", "new"):
        (tmp_path / name).mkdir()
    calls = tmp_path / "calls"
    for name in ("id", "docker", "rsync"):
        file = boundary / name
        file.write_text(
            f"#!{sys.executable}\n"
            + """import os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
root = pathlib.Path(os.environ['TEST_ROOT'])
with (root / 'calls').open('a') as log:
    log.write(name + ' ' + ' '.join(args) + '\\n')
failure = os.environ['TEST_FAILURE']
if name == 'id': print('0')
if name == 'docker':
    if args[:2] == ['ps', '-q'] and failure == 'running': print('running-container')
    if args == ['volume', 'inspect', 'new']: sys.exit(0 if failure == 'target' else 1)
    if '{{.Driver}}' in args: print('local')
    if '{{json .Options}}' in args: print('null')
    if '{{.Mountpoint}}' in args: print(root / args[-1])
if name == 'rsync' and '-aHAXnc' in args and failure == 'verification': print('>f..changed')
"""
        )
        file.chmod(0o755)
    result = subprocess.run(
        ["bash", str(ROOT / "infra/environments/single-host/copy-volume.sh"), "old", "new"],
        env={
            **os.environ,
            "PATH": str(boundary) + os.pathsep + os.environ["PATH"],
            "TEST_ROOT": str(tmp_path),
            "TEST_FAILURE": failure,
        },
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) == (not failure), result.stderr
    log = calls.read_text()
    if failure in {"running", "target"}:
        assert "rsync" not in log
        assert "volume create" not in log
    else:
        assert "rsync -aHAX --numeric-ids" in log
        assert "rsync -aHAXnc --numeric-ids --delete --itemize-changes" in log
    assert "volume rm" not in log
    assert ("Verified volume copy" in result.stdout) == (not failure)
