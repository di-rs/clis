"""Verify Docker's managed containerd shares the owned LogNamespace mount view."""

import os
import tomllib
from pathlib import Path
from typing import Any


def owned_containerd(
    root: Path, daemon_pid: int, *, proc_root: Path = Path("/proc")
) -> dict[str, Any]:
    state = root / "docker-exec/containerd"
    pid = int((state / "containerd.pid").read_text().strip())
    if min(pid, daemon_pid) <= 1 or pid == daemon_pid:
        raise RuntimeError("invalid owned containerd PID")
    daemon, child = proc_root / str(daemon_pid), proc_root / str(pid)
    # Capture both start identities before and after reading the ownership boundary.
    before = [
        (process / "stat").read_text().rsplit(")", 1)[1].split() for process in (daemon, child)
    ]
    if int(before[1][1]) != daemon_pid:
        raise RuntimeError("containerd is not the owned daemon child")
    for process, name in ((daemon, "dockerd"), (child, "containerd")):
        if (process / "exe").resolve().name != name:
            raise RuntimeError("owned runtime executable mismatch")
        if (process / "cgroup").read_text().strip() != f"0::/system.slice/{root.name}.service":
            raise RuntimeError("owned runtime cgroup mismatch")
    namespace = os.readlink(daemon / "ns/mnt")
    if namespace != os.readlink(child / "ns/mnt") or namespace == os.readlink(
        proc_root / "1/ns/mnt"
    ):
        raise RuntimeError("owned containerd mount namespace mismatch")
    if (daemon / "root/run/containerd/containerd.sock").exists():
        raise RuntimeError("shared containerd socket visible to owned daemon")
    config_path = state / "containerd.toml"
    command = (child / "cmdline").read_bytes().split(b"\0")
    if command[1:] != [b"--config", os.fsencode(config_path), b""]:
        raise RuntimeError("owned containerd command mismatch")
    config = tomllib.loads(config_path.read_text())
    expected_root, expected_state = root / "docker-data/containerd/daemon", state / "daemon"
    socket = state / "containerd.sock"
    if (
        config.get("root") != str(expected_root)
        or config.get("state") != str(expected_state)
        or config.get("grpc", {}).get("address") != str(socket)
        or not socket.is_socket()
    ):
        raise RuntimeError("owned containerd configuration/socket mismatch")
    after = [
        (process / "stat").read_text().rsplit(")", 1)[1].split() for process in (daemon, child)
    ]
    if [item[19] for item in before] != [item[19] for item in after]:
        raise RuntimeError("owned runtime process identity changed")
    return {
        "pid": pid,
        "start": before[1][19],
        "daemon_pid": daemon_pid,
        "daemon_start": before[0][19],
        "mount_namespace": namespace,
        "socket": str(socket),
        "root": str(expected_root),
        "state": str(expected_state),
        "shared_socket_hidden": True,
    }
