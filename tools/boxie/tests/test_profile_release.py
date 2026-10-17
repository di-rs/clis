"""Operator profile replacement changes new admissions while retaining old snapshots."""

import json
from pathlib import Path

import pytest
from boxie.domain.errors import BoxieError
from boxie.sandbox.profiles import ProfileRegistry
from boxie_test_support.orchestrator import configuration


def test_atomic_profile_operation_retains_old_admission_and_checks_ack(tmp_path: Path) -> None:
    from boxie_infra.profiles import update_profiles

    config = configuration(tmp_path)
    registry = ProfileRegistry(config.profiles_file, config.data_dir / "profiles")
    old = registry.resolve_profile("shell", ("/bin/sh",), {})
    content = json.loads(config.profiles_file.read_bytes())
    content["shell"]["image_digest"] = "registry.example/shell@sha256:" + "2" * 64
    source_directory = tmp_path / "approved"
    source_directory.mkdir(mode=0o755)
    (source_directory / "profiles.json").write_bytes(config.profiles_file.read_bytes())
    registry.source = source_directory / "profiles.json"
    acknowledged = update_profiles(
        source_directory,
        json.dumps(content).encode(),
        registry.reload,
        lambda: registry.resolve_profile("shell", ("/bin/sh",), {}).config_digest,
    )
    assert acknowledged != old.config_digest
    assert registry.resolve_profile("shell", ("/bin/sh",), {}).image_digest.endswith("2" * 64)
    assert registry.validate_snapshot(old) == old
    prior = registry.source.read_bytes()
    with pytest.raises(BoxieError):
        update_profiles(source_directory, b"{}", registry.reload, lambda: registry.current)
    assert registry.source.read_bytes() == prior


def test_unacknowledged_profiles_are_restored_and_reloaded(tmp_path: Path) -> None:
    from boxie_infra.profiles import ProfileUpdateError, update_profiles

    config = configuration(tmp_path)
    registry = ProfileRegistry(config.profiles_file, config.data_dir / "profiles")
    original = registry.current
    content = json.loads(config.profiles_file.read_bytes())
    content["shell"]["image_digest"] = "registry.example/shell@sha256:" + "2" * 64
    calls = 0

    def reload() -> None:
        nonlocal calls
        calls += 1
        # Simulate the new digest never becoming acknowledged; restoration does.
        if calls > 1:
            registry.reload()

    with pytest.raises(ProfileUpdateError, match="restored"):
        update_profiles(
            config.profiles_file.parent,
            json.dumps(content).encode(),
            reload,
            lambda: registry.current,
            timeout=0.02,
        )
    assert registry.current == original
    assert (
        registry.source.read_bytes()
        == (config.data_dir / "profiles" / f"{original}.json").read_bytes()
    )


@pytest.mark.integration
def test_atomic_profile_child_is_visible_through_existing_directory_bind(tmp_path: Path) -> None:
    import hashlib
    import subprocess

    from boxie_infra.profiles import update_profiles

    config = configuration(tmp_path)
    content = json.loads(config.profiles_file.read_bytes())
    content["shell"]["image_digest"] = "registry.example/shell@sha256:" + "2" * 64
    subprocess.run(
        ["docker", "pull", "python:3.15-slim"], check=True, capture_output=True, timeout=180
    )
    image = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{index .RepoDigests 0}}", "python:3.15-slim"],
        text=True,
    ).strip()
    container = subprocess.check_output(
        [
            "docker",
            "run",
            "-d",
            "--network",
            "none",
            "--read-only",
            "--user",
            "10001:10001",
            "-v",
            f"{config.profiles_file.parent}:/approved:ro",
            image,
            "python",
            "-c",
            "import time; time.sleep(60)",
        ],
        text=True,
    ).strip()
    config.profiles_file.parent.chmod(0o755)
    config.profiles_file.chmod(0o644)
    try:

        def read_digest() -> str:
            return subprocess.check_output(
                [
                    "docker",
                    "exec",
                    container,
                    "python",
                    "-c",
                    "from pathlib import Path; import hashlib; print(hashlib.sha256(Path('/approved/profiles.json').read_bytes()).hexdigest())",
                ],
                text=True,
                timeout=3,
            ).strip()

        digest = update_profiles(
            config.profiles_file.parent, json.dumps(content).encode(), lambda: None, read_digest
        )
        assert digest == hashlib.sha256(json.dumps(content).encode()).hexdigest()
        assert read_digest() == digest
        assert (
            subprocess.check_output(
                ["docker", "inspect", "--format", "{{.Id}}", container], text=True
            ).strip()
            == container
        )
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container], check=True, capture_output=True, timeout=10
        )
