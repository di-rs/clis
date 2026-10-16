"""One owned journald namespace, dockerd and two ext4 pressure filesystems.

Only the ephemeral hosted Linux CI fixture may call this module. The host's
normal journal and Docker daemon are neither restarted nor reconfigured.
"""

import grp
import json
import os
import platform
import re
import shutil
import time
from pathlib import Path
from typing import Any

from observability_infra.smoke_containerd import owned_containerd
from observability_infra.smoke_runtime import Commands, evidence
from observability_infra.smoke_watchdog import process_identity


def require_ephemeral_runner() -> None:
    if (
        platform.system() != "Linux"
        or platform.machine() != "x86_64"
        or os.geteuid() != 0
        or os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
        or not Path("/run/systemd/system").is_dir()
    ):
        raise RuntimeError("requires root on an ephemeral GitHub-hosted Linux amd64 runner")


class Resources:
    def __init__(self, root: Path, commands: Commands) -> None:
        if not re.fullmatch(r"/var/tmp/boxie-observability-[a-f0-9]{12}", str(root)):
            raise ValueError("exact owned root required")
        self.root, self.commands = root, commands
        self.owner = root.name
        self.socket = root / "docker.sock"
        self.unit = self.owner + ".service"
        self.namespace = self.owner
        self.journal_unit = f"systemd-journald@{self.namespace}.service"
        self.manifest = root / "ownership.json"
        self.state: dict[str, Any] = {
            "owner": self.owner,
            "loops": {},
            "mounts": {},
            "files": [],
            "unit": self.unit,
            "journal_unit": self.journal_unit,
        }
        if self.manifest.exists():
            self.state = json.loads(self.manifest.read_text())
            if self.state["owner"] != self.owner:
                raise ValueError("foreign ownership manifest")

    def save(self) -> None:
        evidence(self.manifest, self.state)

    def new_file(self, path: Path, content: str) -> None:
        if path.exists():
            raise RuntimeError("refusing to replace existing fixture path")
        self.state["files"].append(str(path))
        self.save()  # Register intent before mutation, including partially written files.
        with path.open("x") as target:
            target.write(content)

    def filesystem(self, name: str, target: Path, mib: int) -> None:
        image = self.root / (name + ".ext4")
        self.new_file(image, "")
        with image.open("r+b") as stream:
            stream.truncate(mib * 1024**2)
        self.commands.run(["mkfs.ext4", "-q", "-F", "-m", "0", str(image)])
        # A loop is registered immediately after allocation and before mounting.
        device = self.commands.run(["losetup", "--find", "--show", str(image)])
        self.state["loops"][device] = str(image)
        self.save()
        target.mkdir(parents=True, exist_ok=False)
        self.state["mounts"][str(target)] = device
        self.save()
        self.commands.run(["mount", "-o", "nosuid,nodev", device, str(target)])

    def daemon_unit(self) -> str:
        # Docker otherwise auto-connects to the host containerd, whose mount namespace
        # cannot see rootfs mounts created by this LogNamespace-private dockerd.
        # The private filesystem must allow containerd's /run/containerd/s shim sockets.
        return (
            "[Unit]\nDescription=Owned Boxie observability fixture\n"
            "[Service]\nType=exec\nDelegate=yes\nKillMode=mixed\nTimeoutStopSec=15\nMemoryMax=1073741824\nMemorySwapMax=0\n"
            "TemporaryFileSystem=/run/containerd:rw,size=16M,mode=0755\n"
            f"LogNamespace={self.namespace}\n"
            f"ExecStart=/usr/bin/dockerd --config-file={self.root}/daemon.json\n"
            f"StandardOutput=append:{self.root}/dockerd.log\n"
            f"StandardError=append:{self.root}/dockerd.log\n"
        )

    def prepare(self) -> None:
        require_ephemeral_runner()
        self.root.mkdir(mode=0o755, exist_ok=False)
        self.save()
        self.filesystem("vector", self.root / "vector", 640)
        os.chown(self.root / "vector", 65532, 65532)
        machine = Path("/etc/machine-id").read_text().strip()
        if not re.fullmatch("[a-f0-9]{32}", machine):
            raise RuntimeError("machine identity unavailable")
        self.state["journal_path"] = f"/var/log/journal/{machine}.{self.namespace}"
        self.save()
        journal = Path(self.state["journal_path"])
        self.filesystem("journal", journal, 256)
        gid = grp.getgrnam("systemd-journal").gr_gid
        os.chown(journal, 0, gid)
        journal.chmod(0o2755)
        alias = self.root / "journal"
        alias.mkdir()
        self.state["mounts"][str(alias)] = str(journal)
        self.save()
        self.commands.run(["mount", "--bind", str(journal), str(alias)])
        # Explicit reduced pressure profile, distinct from production capacity.
        self.new_file(
            Path(f"/etc/systemd/journald@{self.namespace}.conf"),
            "[Journal]\nStorage=persistent\nSystemMaxUse=64M\nSystemKeepFree=16M\n"
            "RuntimeMaxUse=16M\nRuntimeKeepFree=16M\nMaxFileSec=1h\nMaxRetentionSec=2d\n"
            "SystemMaxFileSize=8M\nRateLimitIntervalSec=0\nCompress=no\n",
        )
        dropin = Path("/run/systemd/system") / (self.journal_unit + ".d")
        self.state.setdefault("directories", []).append(str(dropin))
        self.save()
        dropin.mkdir(exist_ok=False)
        self.new_file(dropin / "smoke.conf", "[Service]\nMemoryMax=268435456\nMemorySwapMax=0\n")
        daemon = {
            "data-root": str(self.root / "docker-data"),
            "exec-root": str(self.root / "docker-exec"),
            "pidfile": str(self.root / "dockerd.pid"),
            "hosts": ["unix://" + str(self.socket)],
            "bridge": "none",
            "iptables": False,
            "ip6tables": False,
            "ip-forward": False,
            "ip-masq": False,
            "userland-proxy": True,
            "runtimes": {
                "runsc": {
                    "path": "/usr/bin/runsc",
                    "runtimeArgs": ["--platform=systrap", "--fdlimit=4096"],
                }
            },
            "labels": ["io.boxie.test.owner=" + self.owner],
        }
        self.new_file(self.root / "daemon.json", json.dumps(daemon))
        self.new_file(
            Path("/run/systemd/system") / self.unit,
            self.daemon_unit(),
        )
        self.commands.run(["systemctl", "daemon-reload"])
        self.commands.run(["systemctl", "start", self.unit])
        while True:
            try:
                info = json.loads(
                    self.commands.run(
                        [
                            "docker",
                            "--host",
                            "unix://" + str(self.socket),
                            "info",
                            "--format",
                            "{{json .}} ",
                        ]
                    )
                )
                break
            except RuntimeError:
                self.commands.deadline.remaining()
                time.sleep(0.2)
        if (
            info["DockerRootDir"] != str(self.root / "docker-data")
            or "io.boxie.test.owner=" + self.owner not in info["Labels"]
        ):
            raise RuntimeError("owned daemon verification failed")
        self.state.update(daemon_id=info["ID"], journal_gid=gid)
        self.state["systemd_cgroups"] = {
            unit: self.commands.run(
                ["systemctl", "show", unit, "--property=ControlGroup", "--value"]
            )
            for unit in (self.unit, self.journal_unit)
        }
        daemon_pid = int(
            self.commands.run(["systemctl", "show", self.unit, "--property=MainPID", "--value"])
        )
        self.state["containerd"] = owned_containerd(self.root, daemon_pid)
        self.state["daemon_mount_namespace"] = self.state["containerd"]["mount_namespace"]
        self.save()

    def owned_cgroup(self, unit: str) -> str:
        if unit not in (self.unit, self.journal_unit):
            raise RuntimeError("foreign owned cgroup unit")
        while True:
            cgroup = self.commands.run(
                ["systemctl", "show", unit, "--property=ControlGroup", "--value"]
            )
            if cgroup:
                break
            self.commands.deadline.remaining(restoring=self.commands.restoring)
            time.sleep(0.1)  # Namespace journald can activate only after the first log writer.
        path = Path(cgroup)
        if not path.is_absolute() or path.name != unit or ".." in path.parts:
            raise RuntimeError("empty/root/foreign owned cgroup")
        pid = self.commands.run(["systemctl", "show", unit, "--property=MainPID", "--value"])
        if not pid.isdecimal() or int(pid) <= 0:
            raise RuntimeError("owned cgroup has no active service process")
        membership = Path(f"/proc/{pid}/cgroup").read_text().strip()
        if membership != "0::" + cgroup:
            raise RuntimeError("owned service cgroup membership mismatch")
        return cgroup

    def close(self) -> dict[str, Any]:
        require_ephemeral_runner()
        errors: list[str] = []
        # The scenario watchdog restoration must precede daemon teardown.
        for manifest in self.root.glob("watch-*.json"):
            if manifest.name.endswith(".result.json"):
                continue
            manifest.with_suffix(".restore").touch()
        # The daemon itself is an owned boundary. Register discovered exact IDs
        # before removal, including resources created immediately before a setup crash.
        if self.socket.exists():
            try:
                base = ["docker", "--host", "unix://" + str(self.socket)]
                info = json.loads(self.commands.run([*base, "info", "--format", "{{json .}} "]))
                if (
                    info["DockerRootDir"] != str(self.root / "docker-data")
                    or "io.boxie.test.owner=" + self.owner not in info["Labels"]
                ):
                    raise RuntimeError("cleanup daemon ownership mismatch")
                inventory = {
                    "containers": self.commands.run([*base, "ps", "--all", "--quiet"]).split(),
                    "networks": [],
                    "volumes": self.commands.run([*base, "volume", "ls", "--quiet"]).split(),
                }
                for identifier in self.commands.run([*base, "network", "ls", "--quiet"]).split():
                    item = json.loads(self.commands.run([*base, "network", "inspect", identifier]))[
                        0
                    ]
                    if item["Name"] not in {"bridge", "host", "none"}:
                        inventory["networks"].append(item["Id"])
                self.state["cleanup_inventory"] = inventory
                self.save()
                for kind, arguments in (
                    ("containers", ["rm", "--force"]),
                    ("networks", ["network", "rm"]),
                    ("volumes", ["volume", "rm"]),
                ):
                    for identifier in inventory[kind]:
                        try:
                            self.commands.run([*base, *arguments, identifier])
                        except (RuntimeError, TimeoutError) as error:
                            errors.append(type(error).__name__ + ":remove:" + kind)
            except (RuntimeError, TimeoutError) as error:
                errors.append(type(error).__name__ + ":owned-daemon-inventory")
        for unit in (
            self.unit,
            f"systemd-journald@{self.namespace}.socket",
            f"systemd-journald-varlink@{self.namespace}.socket",
            self.journal_unit,
        ):
            try:
                loaded = self.commands.run(
                    ["systemctl", "show", unit, "--property=LoadState", "--value"]
                )
                if loaded != "not-found":
                    self.commands.run(["systemctl", "stop", unit])
            except (RuntimeError, TimeoutError) as error:
                errors.append(type(error).__name__ + ":stop:" + unit)
        # Stop every writer before unmounting. A failed stop never permits detach.
        for unit in (self.unit, self.journal_unit):
            pid = self.commands.run(["systemctl", "show", unit, "--property=MainPID", "--value"])
            if pid not in ("", "0"):
                raise RuntimeError("owned writer still running; retaining mounts")
        managed = self.state.get("containerd")
        if managed:
            try:
                if process_identity(managed["pid"]) == managed["start"]:
                    raise RuntimeError("owned containerd still running; retaining mounts")
            except FileNotFoundError:
                pass
        for target in reversed(self.state["mounts"]):
            try:
                if os.path.ismount(target):
                    expected = self.state["mounts"][target]
                    expected = self.state["mounts"].get(expected, expected)
                    actual = self.commands.run(
                        ["findmnt", "--noheadings", "--output", "SOURCE", "--target", target]
                    ).split("[", 1)[0]
                    if actual != expected:
                        raise RuntimeError("mount ownership mismatch")
                    self.commands.run(["umount", target])
            except (RuntimeError, TimeoutError) as error:
                errors.append(type(error).__name__ + ":unmount:" + target)
        for device, image in self.state["loops"].items():
            if device in self.state.get("detached", []):
                continue
            try:
                current = self.commands.run(
                    ["losetup", "--noheadings", "--output", "BACK-FILE", device]
                )
                if current != image:
                    raise RuntimeError("loop ownership mismatch")
                if any(os.path.ismount(p) for p, d in self.state["mounts"].items() if d == device):
                    raise RuntimeError("loop is still mounted")
                self.commands.run(["losetup", "--detach", device])
                self.state.setdefault("detached", []).append(device)
                self.save()
            except (RuntimeError, TimeoutError) as error:
                errors.append(type(error).__name__ + ":detach:" + device)
        if not errors:
            for name in self.state["files"]:
                Path(name).unlink(missing_ok=True)
            for name in self.state.get("directories", []):
                if Path(name).exists():
                    Path(name).rmdir()
            self.commands.run(["systemctl", "daemon-reload"])
            if "journal_path" in self.state:
                journal = Path(self.state["journal_path"])
                if journal.exists():
                    journal.rmdir()
                runtime = Path("/run/log/journal") / journal.name
                if runtime.exists():
                    shutil.rmtree(runtime)
            # Only the owned daemon's data (including its images) may be removed.
            if (self.root / "docker-data").exists():
                shutil.rmtree(self.root / "docker-data")
        result = {
            "errors": errors,
            "mounts_remaining": [p for p in self.state["mounts"] if os.path.ismount(p)],
        }
        evidence(self.root / "cleanup.json", result)
        if errors:
            raise RuntimeError("native fixture restoration incomplete")
        return result
