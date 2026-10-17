"""Shared resource preparation exercises filesystem effects and Docker declarations."""

import ast
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

INSTALLATION = "12345678-1234-4234-8234-123456789abc"


def api() -> Any:
    module = importlib.import_module("host_infra")
    assert hasattr(module, "__path__")
    assert importlib.util.find_spec("host_infra.resources"), "resource preparation missing"
    return importlib.import_module("host_infra.resources")


class Docker:
    def __init__(self) -> None:
        self.networks: dict[str, dict[str, Any]] = {}
        self.volumes: dict[str, dict[str, Any]] = {}
        self.created: list[list[str]] = []
        self.storage_available = True

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        if argv[0].endswith("boxie-storage-guard"):
            if not self.storage_available:
                raise subprocess.CalledProcessError(1, argv)
            return subprocess.CompletedProcess(argv, 0, "", "")
        assert argv[0] == "docker"
        kind, command = argv[1:3]
        collection = self.networks if kind == "network" else self.volumes
        if command == "ls":
            return subprocess.CompletedProcess(argv, 0, "\n".join(collection), "")
        if command == "inspect":
            return subprocess.CompletedProcess(argv, 0, json.dumps([collection[argv[3]]]), "")
        assert command == "create"
        self.created.append(argv)
        labels = {
            argv[i + 1].split("=", 1)[0]: argv[i + 1].split("=", 1)[1]
            for i, value in enumerate(argv)
            if value == "--label"
        }
        options = {
            argv[i + 1].split("=", 1)[0]: argv[i + 1].split("=", 1)[1]
            for i, value in enumerate(argv)
            if value == "--opt"
        }
        collection[argv[-1]] = {
            "Name": argv[-1],
            "Labels": labels,
            "Driver": "bridge" if kind == "network" else "local",
            "Internal": "--internal" in argv,
            "Options": options,
        }
        return subprocess.CompletedProcess(argv, 0, "created-id", "")


def plan(root: Path) -> Any:
    a = api()
    return a.ResourcePlan(
        installation_id=INSTALLATION,
        mount_path=str(root),
        storage_device="/dev/disk/by-id/scsi-0HC_Volume_42",
        networks=(a.Network("test-control", True), a.Network("test-proxy", False)),
        volumes=(a.Volume("test-db", "volumes/postgres", os.getuid(), os.getgid()),),
        directories=(a.Directory("boxie", os.getuid(), os.getgid()),),
    )


def test_ensure_preserves_owned_resources_and_uses_only_persistent_storage(tmp_path: Path) -> None:
    p = plan(tmp_path)
    docker = Docker()
    api().ensure(p, run=docker)
    assert docker.networks["test-control"]["Internal"]
    assert not docker.networks["test-proxy"]["Internal"]
    assert docker.volumes["test-db"]["Options"] == {
        "type": "none",
        "o": "bind",
        "device": str(tmp_path / "volumes/postgres"),
    }
    assert docker.volumes["test-db"]["Labels"] == {
        "clis.installation": INSTALLATION,
        "clis.owner": "platform",
    }
    assert (tmp_path / "boxie").is_dir()
    assert (tmp_path / "volumes/postgres").stat().st_mode & 0o777 == 0o700
    assert len(docker.created) == 3
    api().ensure(p, run=docker)
    assert len(docker.created) == 3


def test_missing_disk_prevents_any_resource_or_directory_creation(tmp_path: Path) -> None:
    docker = Docker()
    docker.storage_available = False
    with pytest.raises(subprocess.CalledProcessError):
        api().ensure(plan(tmp_path), run=docker)
    assert not docker.created and not (tmp_path / "volumes").exists()


@pytest.mark.parametrize("mutation", ["foreign", "public", "wrong-disk"])
def test_existing_resource_mismatch_fails_without_repair(tmp_path: Path, mutation: str) -> None:
    docker = Docker()
    p = plan(tmp_path)
    api().ensure(p, run=docker)
    before = list(docker.created)
    if mutation == "foreign":
        docker.networks["test-control"]["Labels"] = {"clis.installation": "foreign"}
    elif mutation == "public":
        docker.networks["test-control"]["Internal"] = False
    else:
        docker.volumes["test-db"]["Options"]["device"] = "/var/lib/wrong-disk"
    with pytest.raises(ValueError, match="existing"):
        api().ensure(p, run=docker)
    assert docker.created == before


def test_storage_paths_cannot_escape_through_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "volumes").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        api().ensure(plan(root), run=Docker())


@pytest.mark.parametrize("directory", ["../escape", "/var/lib/root-disk", "a/../../b", "a//b"])
def test_plan_rejects_noncanonical_or_escaping_paths(directory: str) -> None:
    with pytest.raises(ValueError):
        api().Volume("db", directory, 999, 999)


def test_host_preparation_runs_standalone_with_python312_compatible_syntax() -> None:
    script = Path(__file__).parents[1] / "host_infra/resources.py"
    ast.parse(script.read_text(), filename=str(script), feature_version=(3, 12))
    result = subprocess.run(
        [sys.executable, "-I", str(script), "--help"],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    assert "--plan" in result.stdout


def test_explicit_group_reader_mode_survives_restrictive_operator_umask(tmp_path: Path) -> None:
    a = api()
    declaration = a.ResourcePlan(
        INSTALLATION,
        "/dev/disk/by-id/scsi-0HC_Volume_42",
        str(tmp_path),
        (),
        (),
        (a.Directory("approved-profiles", os.getuid(), os.getgid(), 0o750),),
    )
    previous = os.umask(0o077)
    try:
        a.ensure(declaration, run=Docker())
    finally:
        os.umask(previous)
    assert (tmp_path / "approved-profiles").stat().st_mode & 0o777 == 0o750
    a.ensure(declaration, run=Docker())
