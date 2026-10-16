"""CLI expectations are hand-derived from the release contract, not snapshots."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[2]
DIGEST = "registry.example/test@sha256:" + "a" * 64


def invoke(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "deploy_tool", *args],
        env={**os.environ, "PYTHONPATH": str(PACKAGE), **(env or {})},
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )


def test_prepare_private_content_addressed_release(source: Path) -> None:
    # Omitting environment from the release hash would let an existing release be overwritten.
    args = (
        "--descriptor",
        str(source / "descriptor.json"),
        "--release",
        str(source / "release.json"),
    )
    result = invoke("validate", *args)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"component": "sample", "revision": "1" * 40, "valid": True}
    out = source / "prepared"
    result = invoke(
        "prepare", *args, "--environment", str(source / "env.json"), "--output", str(out)
    )
    assert result.returncode == 0, result.stderr
    first = json.loads(result.stdout)["release_id"]
    assert len(first) == 64
    assert "never-log-me" not in result.stdout + result.stderr
    assert (out / "environment.json").stat().st_mode & 0o777 == 0o600
    assert json.loads((out / "environment.json").read_text())["TOKEN"] == "never-log-me"
    assert (out / "files/compose.yml").read_text() == (source / "compose.yml").read_text()
    (source / "env.json").write_text('{"TOKEN":"new-secret"}')
    result = invoke(
        "prepare",
        *args,
        "--environment",
        str(source / "env.json"),
        "--output",
        str(source / "second"),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["release_id"] != first


@pytest.mark.parametrize("fault", ["mutable", "component", "path", "service", "schema"])
def test_invalid_target_is_rejected_before_preparation(source: Path, fault: str) -> None:
    # Each fault would activate unpinned code, escape the component, or erase rollback compatibility.
    descriptor = json.loads((source / "descriptor.json").read_text())
    release = json.loads((source / "release.json").read_text())
    if fault == "mutable":
        release["images"]["APP_IMAGE"] = "registry.example/test:latest"
    elif fault == "component":
        release["component"] = "other"
    elif fault == "path":
        descriptor["files"] = ["../outside"]
    elif fault == "service":
        descriptor["services"] = ["other"]
    else:
        release["compatibility"] = {}
    (source / "descriptor.json").write_text(json.dumps(descriptor))
    (source / "release.json").write_text(json.dumps(release))
    result = invoke(
        "validate",
        "--descriptor",
        str(source / "descriptor.json"),
        "--release",
        str(source / "release.json"),
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.startswith("deploy: ")


def test_prepare_missing_component_secret_is_private_error(source: Path) -> None:
    (source / "env.json").write_text('{"OTHER":"never-log-me"}')
    result = invoke(
        "prepare",
        "--descriptor",
        str(source / "descriptor.json"),
        "--release",
        str(source / "release.json"),
        "--environment",
        str(source / "env.json"),
        "--output",
        str(source / "prepared"),
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert "never-log-me" not in result.stderr
    assert not (source / "prepared").exists()


def test_deploy_pins_host_and_keeps_private_payload_off_arguments(source: Path) -> None:
    out = source / "prepared"
    prepared = invoke(
        "prepare",
        "--descriptor",
        str(source / "descriptor.json"),
        "--release",
        str(source / "release.json"),
        "--environment",
        str(source / "env.json"),
        "--output",
        str(out),
    )
    assert prepared.returncode == 0
    identity = source / "identity"
    identity.write_text("private-key-never-print")
    identity.chmod(0o600)
    profile = source / "profile.json"
    profile.write_text(
        json.dumps(
            {
                "host": "192.0.2.7",
                "port": 2222,
                "host_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEY5jUnHMEJzWwU5V1fAG0vQZ6nNoMFhD3w2M2rLm1dX",
                "identity_ref": "DEPLOY_SSH_KEY",
            }
        )
    )
    bin_dir = source / "bin"
    bin_dir.mkdir()
    ssh = bin_dir / "ssh"
    ssh.write_text("""#!/usr/bin/env python3
import hashlib, json, pathlib, sys
args = sys.argv[1:]
payload = json.load(sys.stdin)
assert all('never-log-me' not in item and 'private-key-never-print' not in item for item in args)
assert payload['environment']['TOKEN'] == 'never-log-me'
assert 'StrictHostKeyChecking=yes' in args
known = next(arg.split('=', 1)[1] for arg in args if arg.startswith('UserKnownHostsFile='))
assert pathlib.Path(known).read_text().startswith('[192.0.2.7]:2222 ssh-ed25519 ')
assert 'root@192.0.2.7' in args
assert pathlib.Path(known).stat().st_mode & 0o777 == 0o600
release_id = hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
print(json.dumps({'component':'sample','release_id':release_id,'outcome':'activated'}))
""")
    ssh.chmod(0o700)
    result = invoke(
        "deploy",
        "--prepared",
        str(out),
        "--profile",
        str(profile),
        "--identity",
        str(identity),
        env={"PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]},
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["outcome"] == "activated"
    assert "never-log-me" not in result.stdout + result.stderr
    assert "private-key-never-print" not in result.stdout + result.stderr


def test_tampered_preparation_never_starts_transport(source: Path) -> None:
    out = source / "prepared"
    assert (
        invoke(
            "prepare",
            "--descriptor",
            str(source / "descriptor.json"),
            "--release",
            str(source / "release.json"),
            "--environment",
            str(source / "env.json"),
            "--output",
            str(out),
        ).returncode
        == 0
    )
    (out / "environment.json").write_text('{"TOKEN":"tampered-secret"}')
    result = invoke(
        "deploy",
        "--prepared",
        str(out),
        "--profile",
        str(source / "not-a-profile"),
        "--identity",
        str(source / "not-an-identity"),
    )
    assert result.returncode == 2
    assert "prepared release content changed" in result.stderr
    assert "tampered-secret" not in result.stderr


def test_required_compose_image_syntax_is_valid(source: Path) -> None:
    path = source / "compose.yml"
    path.write_text(path.read_text().replace("${APP_IMAGE}", "${APP_IMAGE:?Set immutable image}"))
    result = invoke(
        "validate",
        "--descriptor",
        str(source / "descriptor.json"),
        "--release",
        str(source / "release.json"),
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("key", ["CLIS_RELEASE_REVISION", "DOCKER_HOST", "COMPOSE_FILE", "PATH"])
def test_runtime_target_overrides_are_rejected(source: Path, key: str) -> None:
    (source / "env.json").write_text(json.dumps({"TOKEN": "secret", key: "unsafe-override"}))
    result = invoke(
        "prepare",
        "--descriptor",
        str(source / "descriptor.json"),
        "--release",
        str(source / "release.json"),
        "--environment",
        str(source / "env.json"),
        "--output",
        str(source / "prepared"),
    )
    assert result.returncode == 2
    assert not (source / "prepared").exists()


def test_public_prepared_environment_is_rejected(source: Path) -> None:
    out = source / "prepared"
    assert (
        invoke(
            "prepare",
            "--descriptor",
            str(source / "descriptor.json"),
            "--release",
            str(source / "release.json"),
            "--environment",
            str(source / "env.json"),
            "--output",
            str(out),
        ).returncode
        == 0
    )
    (out / "environment.json").chmod(0o644)
    result = invoke(
        "deploy",
        "--prepared",
        str(out),
        "--profile",
        str(source / "missing"),
        "--identity",
        str(source / "missing-key"),
    )
    assert result.returncode == 2
    assert "private" in result.stderr


def test_file_through_internal_symlink_is_rejected(source: Path) -> None:
    (source / "real").mkdir()
    (source / "real/config").write_text("content")
    (source / "symlink").symlink_to(source / "real", target_is_directory=True)
    descriptor = json.loads((source / "descriptor.json").read_text())
    descriptor["files"] = ["symlink/config"]
    (source / "descriptor.json").write_text(json.dumps(descriptor))
    result = invoke(
        "validate",
        "--descriptor",
        str(source / "descriptor.json"),
        "--release",
        str(source / "release.json"),
    )
    assert result.returncode == 2


def test_public_ssh_identity_is_rejected_without_disclosing_key(source: Path) -> None:
    out = source / "prepared"
    assert (
        invoke(
            "prepare",
            "--descriptor",
            str(source / "descriptor.json"),
            "--release",
            str(source / "release.json"),
            "--environment",
            str(source / "env.json"),
            "--output",
            str(out),
        ).returncode
        == 0
    )
    key = source / "identity"
    key.write_text("private-key-never-print")
    key.chmod(0o644)
    profile = source / "profile.json"
    profile.write_text(
        json.dumps(
            {
                "host": "192.0.2.7",
                "identity_ref": "DEPLOY_SSH_KEY",
                "host_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEY5jUnHMEJzWwU5V1fAG0vQZ6nNoMFhD3w2M2rLm1dX",
            }
        )
    )
    result = invoke(
        "deploy", "--prepared", str(out), "--profile", str(profile), "--identity", str(key)
    )
    assert result.returncode == 2
    assert "mode 0600" in result.stderr
    assert "private-key-never-print" not in result.stderr


def test_remote_metadata_filename_cannot_be_component_payload(source: Path) -> None:
    descriptor = json.loads((source / "descriptor.json").read_text())
    descriptor["files"] = ["environment.json"]
    (source / "environment.json").write_text("source-owned content")
    (source / "descriptor.json").write_text(json.dumps(descriptor))
    result = invoke(
        "validate",
        "--descriptor",
        str(source / "descriptor.json"),
        "--release",
        str(source / "release.json"),
    )
    assert result.returncode == 2
