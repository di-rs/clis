"""Explicit shared-resource preparation, separate from cloud provisioning and releases."""

import argparse
import json
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID

_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}\Z")
Run = Callable[[list[str]], subprocess.CompletedProcess[str]]


def _relative(value: str) -> None:
    if (
        not re.fullmatch(r"[a-zA-Z0-9_./-]+", value)
        or PurePosixPath(value).is_absolute()
        or str(PurePosixPath(value)) != value
        or ".." in PurePosixPath(value).parts
        or value == "."
    ):
        raise ValueError("canonical relative storage directory required")


@dataclass(frozen=True)
class Network:
    name: str
    internal: bool = True

    def __post_init__(self) -> None:
        if not _NAME.fullmatch(self.name) or type(self.internal) is not bool:
            raise ValueError("invalid network declaration")


@dataclass(frozen=True)
class Directory:
    directory: str
    uid: int
    gid: int
    mode: int = 0o700

    def __post_init__(self) -> None:
        _relative(self.directory)
        if type(self.mode) is not int or self.mode not in (0o700, 0o750, 0o755):
            raise ValueError("unsafe directory reader mode")
        if any(type(v) is not int or not 0 <= v < 2**31 for v in (self.uid, self.gid)):
            raise ValueError("explicit numeric directory owner required")


@dataclass(frozen=True)
class Volume:
    name: str
    directory: str
    uid: int
    gid: int

    def __post_init__(self) -> None:
        if not _NAME.fullmatch(self.name):
            raise ValueError("invalid volume declaration")
        Directory(self.directory, self.uid, self.gid)


@dataclass(frozen=True)
class ResourcePlan:
    installation_id: str
    storage_device: str
    mount_path: str
    networks: tuple[Network, ...]
    volumes: tuple[Volume, ...]
    directories: tuple[Directory, ...] = ()

    def __post_init__(self) -> None:
        if str(UUID(self.installation_id)) != self.installation_id:
            raise ValueError("canonical installation UUID required")
        if not re.fullmatch(r"/dev/disk/by-id/scsi-0HC_Volume_[0-9]+", self.storage_device):
            raise ValueError("explicit persistent storage device required")
        mount = PurePosixPath(self.mount_path)
        if (
            not mount.is_absolute()
            or str(mount) != self.mount_path
            or ".." in mount.parts
            or self.mount_path == "/"
        ):
            raise ValueError("canonical nonroot persistent mount required")
        for values in (self.networks, self.volumes):
            if len({value.name for value in values}) != len(values):
                raise ValueError("duplicate shared resource name")
        paths = [v.directory for v in self.volumes] + [v.directory for v in self.directories]
        if len(paths) != len(set(paths)):
            raise ValueError("shared data directories must be independent")
        if any(
            a != b and PurePosixPath(a) in PurePosixPath(b).parents for a in paths for b in paths
        ):
            raise ValueError("shared data directories must not overlap")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, check=True, capture_output=True, text=True, timeout=30)


def _directory(root: Path, value: Volume | Directory, *, create: bool) -> None:
    path = root / value.directory
    for part in (root, *path.relative_to(root).parents, path):
        # Relative parents are joined below, while root/path are already absolute.
        checked = part if part.is_absolute() else root / part
        if checked.is_symlink():
            raise ValueError("symlink in persistent storage path")
    if path.exists():
        owner = path.stat()
        if not path.is_dir() or (owner.st_uid, owner.st_gid) != (value.uid, value.gid):
            raise ValueError("existing storage directory has a different owner")
        if isinstance(value, Directory) and owner.st_mode & 0o777 != value.mode:
            raise ValueError("existing storage directory has different reader permissions")
    elif create:
        path.mkdir(mode=value.mode if isinstance(value, Directory) else 0o700, parents=True)
        owner = path.stat()
        if (owner.st_uid, owner.st_gid) != (value.uid, value.gid):
            os.chown(path, value.uid, value.gid)


def _inspect(kind: str, run: Run) -> dict[str, dict[str, Any]]:
    names = run(["docker", kind, "ls", "--format", "{{.Name}}"]).stdout.split()
    result = {}
    for name in names:
        result[name] = json.loads(run(["docker", kind, "inspect", name]).stdout)[0]
    return result


def ensure(plan: ResourcePlan, *, run: Run = _run) -> None:
    """Create absent owned resources; never repair, adopt, delete, or activate workloads."""
    run(["/usr/local/sbin/boxie-storage-guard", plan.mount_path, plan.storage_device])
    labels = {"clis.installation": plan.installation_id, "clis.owner": "platform"}
    networks = _inspect("network", run)
    volumes = _inspect("volume", run)
    for network in plan.networks:
        if existing := networks.get(network.name):
            if (
                not labels.items() <= (existing.get("Labels") or {}).items()
                or existing.get("Internal") is not network.internal
                or existing.get("Driver") != "bridge"
            ):
                raise ValueError("existing network has different ownership or isolation")
    for volume in plan.volumes:
        if existing := volumes.get(volume.name):
            if (
                not labels.items() <= (existing.get("Labels") or {}).items()
                or existing.get("Driver") != "local"
                or existing.get("Options")
                != {
                    "type": "none",
                    "o": "bind",
                    "device": str(Path(plan.mount_path) / volume.directory),
                }
            ):
                raise ValueError("existing volume has different ownership or backing storage")
    directories: tuple[Volume | Directory, ...] = (*plan.volumes, *plan.directories)
    for directory in directories:
        _directory(Path(plan.mount_path), directory, create=False)
    # Validate all existing resources before making the first mutation.
    for directory in directories:
        _directory(Path(plan.mount_path), directory, create=True)
    label_args = [
        argument for key, value in labels.items() for argument in ("--label", f"{key}={value}")
    ]
    for network in plan.networks:
        if network.name not in networks:
            run(
                [
                    "docker",
                    "network",
                    "create",
                    "--driver",
                    "bridge",
                    *label_args,
                    *(["--internal"] if network.internal else []),
                    network.name,
                ]
            )
    for volume in plan.volumes:
        if volume.name not in volumes:
            run(
                [
                    "docker",
                    "volume",
                    "create",
                    "--driver",
                    "local",
                    *label_args,
                    "--opt",
                    "type=none",
                    "--opt",
                    "o=bind",
                    "--opt",
                    f"device={Path(plan.mount_path) / volume.directory}",
                    volume.name,
                ]
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    args = parser.parse_args()
    value = json.loads(args.plan.read_text())
    # Explicit fields reject malformed/extra plan fields before touching the host.
    plan = ResourcePlan(
        installation_id=value.pop("installation_id"),
        storage_device=value.pop("storage_device"),
        mount_path=value.pop("mount_path"),
        networks=tuple(Network(**item) for item in value.pop("networks")),
        volumes=tuple(Volume(**item) for item in value.pop("volumes")),
        directories=tuple(Directory(**item) for item in value.pop("directories", [])),
    )
    if value:
        parser.error("unknown resource plan fields")
    ensure(plan)


if __name__ == "__main__":
    main()
