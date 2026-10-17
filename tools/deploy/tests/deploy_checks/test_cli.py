"""CLI expectations are hand-derived from the release contract, not snapshots."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[2]
DIGEST = "registry.example/test@sha256:" + "a" * 64


def invoke(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    # Existing engine cases use a committed fixture; explicit --repository cases
    # below exercise provenance independently without this fixture preparation.
    if args[0] in {"prepare", "validate"} and "--repository" not in args:
        from .support import commit_source

        arguments = list(args)
        descriptor_path = Path(arguments[arguments.index("--descriptor") + 1])
        source = descriptor_path.parent
        descriptor = json.loads(descriptor_path.read_text())
        revision = commit_source(source)
        release_path = Path(arguments[arguments.index("--release") + 1])
        release = json.loads(release_path.read_text())
        wanted = {
            "descriptor.json",
            descriptor.get("compose_file", "compose.yml"),
            *descriptor.get("files", []),
        }
        if descriptor.get("pre_activate"):
            wanted.add(descriptor["pre_activate"])
        release.update(
            revision=revision,
            settings=release.get("settings", {"TOKEN_REF": "test/v1"}),
            configuration_hashes={
                f: hashlib.sha256((source / f).read_bytes()).hexdigest()
                for f in wanted
                if (source / f).is_file()
            },
        )
        release_path.write_text(json.dumps(release))
        arguments[arguments.index("--descriptor") + 1] = descriptor_path.name
        args = (*arguments, "--repository", str(source))
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
    assert json.loads(result.stdout) == {
        "component": "sample",
        "revision": json.loads((source / "release.json").read_text())["revision"],
        "valid": True,
    }
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
    assert json.loads(result.stdout)["release_id"] == first


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


@pytest.mark.parametrize("budget", [60, 900])
def test_deploy_pins_host_and_keeps_private_payload_off_arguments(
    source: Path, budget: int
) -> None:
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
release_id = hashlib.sha256(json.dumps(payload["release"],sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
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
        "--timeout",
        str(budget),
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


@pytest.mark.parametrize("fault", ["revision", "symlink", "duplicate", "oversize"])
def test_committed_release_rejects_invalid_source_before_output(source: Path, fault: str) -> None:
    from .support import commit_source

    if fault == "symlink":
        (source / "real.yml").write_bytes((source / "compose.yml").read_bytes())
        (source / "compose.yml").unlink()
        (source / "compose.yml").symlink_to("real.yml")
    elif fault == "duplicate":
        p = source / "descriptor.json"
        p.write_text(
            p.read_text().replace(
                '"component": "sample"', '"component": "sample", "component": "sample"'
            )
        )
    elif fault == "oversize":
        p = source / "descriptor.json"
        p.write_text(p.read_text() + " " * 65536)
    revision = commit_source(source)
    release = json.loads((source / "release.json").read_text())
    release["revision"] = "0" * 40 if fault == "revision" else revision
    (source / "release.json").write_text(json.dumps(release))
    out = source / "invalid-prepared"
    result = invoke(
        "prepare",
        "--repository",
        str(source),
        "--descriptor",
        "descriptor.json",
        "--release",
        str(source / "release.json"),
        "--environment",
        str(source / "env.json"),
        "--output",
        str(out),
    )
    assert result.returncode == 2
    assert not out.exists()
    assert "never-log-me" not in result.stdout + result.stderr


def test_release_uses_committed_bytes_and_public_identity(source: Path) -> None:
    from .support import commit_source

    revision = commit_source(source)
    committed = (source / "compose.yml").read_bytes()
    release = json.loads((source / "release.json").read_text())
    release["revision"] = revision
    (source / "images.json").write_text(json.dumps(release["images"]))
    (source / "settings.json").write_text('{"TOKEN_REF":"test/v1"}')
    result = invoke(
        "manifest",
        "--repository",
        str(source),
        "--descriptor",
        "descriptor.json",
        "--revision",
        revision,
        "--images",
        str(source / "images.json"),
        "--settings",
        str(source / "settings.json"),
        "--output",
        str(source / "public.json"),
    )
    assert result.returncode == 0, result.stderr
    public = json.loads((source / "public.json").read_text())
    assert "configuration_hashes" in public
    assert "never-log-me" not in (source / "public.json").read_text()
    (source / "compose.yml").write_text("uncommitted malicious configuration")
    result = invoke(
        "prepare",
        "--repository",
        str(source),
        "--descriptor",
        "descriptor.json",
        "--release",
        str(source / "public.json"),
        "--environment",
        str(source / "env.json"),
        "--output",
        str(source / "committed-prepared"),
    )
    assert result.returncode == 0, result.stderr
    assert (source / "committed-prepared/files/compose.yml").read_bytes() == committed


def test_public_identity_tracks_images_and_references_without_secret_values(source: Path) -> None:
    first = invoke(
        "prepare",
        "--descriptor",
        str(source / "descriptor.json"),
        "--release",
        str(source / "release.json"),
        "--environment",
        str(source / "env.json"),
        "--output",
        str(source / "first-public"),
    )
    assert first.returncode == 0, first.stderr
    public = json.loads((source / "first-public/release.json").read_text())
    assert "never-log-me" not in json.dumps(public)
    release = json.loads((source / "release.json").read_text())
    release["images"]["APP_IMAGE"] = "registry.example/test@sha256:" + "b" * 64
    release["settings"]["TOKEN_REF"] = "test/v2"
    (source / "release.json").write_text(json.dumps(release))
    second = invoke(
        "prepare",
        "--descriptor",
        str(source / "descriptor.json"),
        "--release",
        str(source / "release.json"),
        "--environment",
        str(source / "env.json"),
        "--output",
        str(source / "second-public"),
    )
    assert second.returncode == 0, second.stderr
    assert json.loads(first.stdout)["release_id"] != json.loads(second.stdout)["release_id"]


@pytest.mark.parametrize("budget", [59, 901])
def test_invalid_budget_is_rejected_before_ssh(source: Path, budget: int) -> None:
    from deploy_tool.manifests import ReleaseError
    from deploy_tool.transport import deploy_ssh

    with pytest.raises(ReleaseError, match="between 60 and 900"):
        deploy_ssh({}, {}, source / "missing", budget)


@pytest.mark.parametrize("stream", [1, 2])
def test_ssh_output_is_bounded_while_process_runs(source: Path, stream: int) -> None:
    import time

    from deploy_tool.manifests import ReleaseError
    from deploy_tool.transport import run_ssh

    start = time.monotonic()
    with pytest.raises(ReleaseError, match="output exceeds limit"):
        run_ssh(
            [
                sys.executable,
                "-c",
                f"import os,time; os.write({stream}, b'x'*65536); time.sleep(8)",
            ],
            b"{}",
            10,
        )
    assert time.monotonic() - start < 3
