"""Bounded setup-failure observations before owned Compose teardown."""

import json
import os
import re
import stat
from pathlib import Path
from typing import Any
from urllib.parse import quote

from observability_infra.smoke_runtime import Commands, Deadline, evidence
from observability_infra.smoke_watchdog import process_identity


def safe_runtime_error(message: str, private: tuple[str, ...]) -> str:
    """Retain runtime path/errno context, never arbitrary oversized/env dumps."""
    if len(message.encode()) > 8192:
        return "[oversized runtime error omitted]"
    if re.search(r"(?i)\bEnv[\"']?\s*[=:]|-----BEGIN|\x00|authorization\s*:", message):
        return "[suspicious runtime error omitted]"
    for value in sorted(set(private), key=len, reverse=True):
        if value:
            for encoded in (value, quote(value, safe=""), json.dumps(value)[1:-1]):
                message = message.replace(encoded, "[redacted]")
    message = re.sub(
        r"(?i)((?:password|passwd|token|secret|cookie|api[_-]?key)\s*[=:]\s*)[^\s;,]+",
        r"\1[redacted]",
        message,
    )
    message = re.sub(r"(?i)bearer\s+[^\s;,]+", "Bearer [redacted]", message)
    message = re.sub(
        r"(?<![A-Za-z0-9])[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]*",
        "[redacted-token]",
        message,
    )
    message = re.sub(
        r"(?<![A-Za-z0-9_/])[A-Za-z0-9_+=-]{40,}(?![A-Za-z0-9_/-])", "[redacted-opaque]", message
    )
    message = "".join(char if char >= " " and char != "\x7f" else " " for char in message)
    return message[:4096] + (" [truncated]" if len(message) > 4096 else "")


def daemon_path(proc: Path) -> str:
    # Select only PATH from a bounded proc read; no other environment field leaves this function.
    with (proc / "environ").open("rb") as stream:
        data = stream.read(65537)
    if len(data) > 65536:
        raise ValueError("bounded daemon PATH unavailable")
    values = [entry[5:].decode() for entry in data.split(b"\0") if entry.startswith(b"PATH=")]
    if (
        len(values) != 1
        or len(values[0]) > 4096
        or any(
            not part.startswith("/") or any(ord(c) < 32 for c in part)
            for part in values[0].split(":")
        )
    ):
        raise ValueError("absolute daemon PATH required")
    return values[0]


def image_path(environment: list[str], private: tuple[str, ...]) -> str:
    # Image/container Config.Env is inspected in memory; only a bounded PATH leaves here.
    paths = [item[5:] for item in environment if item.startswith("PATH=")]
    if (
        len(paths) != 1
        or len(paths[0]) > 4096
        or any(
            not part.startswith("/") or any(ord(char) < 32 for char in part)
            for part in paths[0].split(":")
        )
    ):
        return "[PATH unavailable]"
    return safe_runtime_error(paths[0], private)


def source_kind(path: Path) -> str:
    try:
        mode = path.stat().st_mode
        return "file" if stat.S_ISREG(mode) else "directory" if stat.S_ISDIR(mode) else "other"
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unavailable"


def runtime_executables(base: Path, search: str, private: tuple[str, ...]) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for name in ("runc", "containerd", "containerd-shim-runc-v2", "runsc"):
        found = None
        for directory in search.split(":"):
            candidate = base / directory.lstrip("/") / name
            if source_kind(candidate) == "file" and os.access(candidate, os.X_OK):
                found = str(Path(directory) / name)
                break
        result[name] = safe_runtime_error(found, private) if found else None
    return result


def daemon_runtime(
    root: Path, commands: Commands, env: dict[str, str], private: tuple[str, ...]
) -> tuple[dict[str, Any], Path | None]:
    result: dict[str, Any] = {}
    try:
        pid = commands.run(
            ["systemctl", "show", root.name + ".service", "--property=MainPID", "--value"], env=env
        )
        if not pid.isdecimal() or int(pid) <= 0:
            raise ValueError("owned daemon PID unavailable")
        identity = process_identity(int(pid))
        proc = Path("/proc") / pid
        if (proc / "exe").resolve().name != "dockerd":
            raise ValueError("owned daemon executable mismatch")
        search = daemon_path(proc)
        base = proc / "root"
        result["path"] = safe_runtime_error(search, private)
        result["executables"] = runtime_executables(base, search, private)
        if process_identity(int(pid)) != identity:
            raise ValueError("owned daemon process identity changed")
        return result, base
    except (OSError, ValueError, RuntimeError, TimeoutError) as error:
        result["error_type"] = type(error).__name__
        return result, None


def setup_diagnostics(
    root: Path,
    stack: Any,
    failed: dict[str, Any] | None,
    sentinels: tuple[str, ...] = (),
    *,
    failed_setup_output: bytes | None = None,
) -> None:
    commands = Commands(Deadline(15, reserve=0.1))
    result: dict[str, Any] = {"failed_command": failed, "containers": [], "networks": []}
    env = stack.env
    private = tuple(
        str(value)
        for key, value in env.items()
        if re.search(r"(?i)password|secret|token|cookie|credential|key", key)
    )
    private += tuple(
        str(getattr(stack, name)) for name in ("consumer", "deploy") if getattr(stack, name, None)
    )
    private += sentinels
    if (
        failed
        and failed.get("operation") in {"docker.compose.build", "docker.compose.port"}
        and failed_setup_output
    ):
        operation = failed["operation"].rsplit(".", 1)[1]
        result["failed_command"] = {
            **failed,
            "redacted_" + operation + "_error": safe_runtime_error(
                failed_setup_output.decode("utf-8", errors="replace"), private
            ),
        }
    try:
        info = json.loads(commands.run(["docker", "info", "--format", "{{json .}}"], env=env))
        if (
            info["DockerRootDir"] != str(root / "docker-data")
            or "io.boxie.test.owner=" + root.name not in info["Labels"]
        ):
            raise RuntimeError("diagnostic daemon ownership mismatch")
        result["daemon_runtime"], daemon_root = daemon_runtime(root, commands, env, private)
        for name, arguments in (
            ("docker_client", ["docker", "version", "--format", "{{.Client.Version}}"]),
            ("docker_server", ["docker", "version", "--format", "{{.Server.Version}}"]),
            ("compose", ["docker", "compose", "version", "--short"]),
        ):
            version = commands.run(arguments, env=env)
            result[name] = (
                version
                if re.fullmatch(r"v?[0-9][0-9A-Za-z.+_-]{0,64}", version)
                else "unrecognized"
            )
        for project in (stack.postgres, stack.temporal, stack.runtime, root.name + "-telemetry"):
            ids = commands.run(
                [
                    "docker",
                    "ps",
                    "--all",
                    "--quiet",
                    "--filter",
                    "label=com.docker.compose.project=" + project,
                ],
                env=env,
            ).split()
            for identifier in ids[:32]:
                if len(result["containers"]) >= 16:
                    result["container_limit_reached"] = True
                    break
                item = json.loads(commands.run(["docker", "inspect", identifier], env=env))[0]
                if item["Config"]["Labels"].get(
                    "com.docker.compose.project"
                ) != project or not item["Id"].startswith(identifier):
                    raise RuntimeError("foreign diagnostic container")
                state = item["State"]
                detail: dict[str, Any] = {}
                if state["Status"] == "created" and state["ExitCode"] != 0:
                    detail["start_error"] = safe_runtime_error(str(state.get("Error", "")), private)
                    detail["path"] = image_path(item["Config"].get("Env") or [], private)
                    detail["runtime"] = safe_runtime_error(
                        str(item["HostConfig"]["Runtime"]), private
                    )
                    detail["binds"] = [
                        {
                            "source": safe_runtime_error(mount["Source"], private)[:256],
                            "destination": safe_runtime_error(mount["Destination"], private)[:256],
                            "source_kind_in_daemon": source_kind(
                                daemon_root / mount["Source"].lstrip("/")
                            )
                            if daemon_root
                            else "unavailable",
                        }
                        for mount in item.get("Mounts", [])[:8]
                        if mount["Type"] == "bind"
                    ]
                result["containers"].append(
                    {
                        **detail,
                        "service": safe_runtime_error(
                            str(item["Config"]["Labels"].get("com.docker.compose.service", "")),
                            private,
                        )[:64],
                        "id": item["Id"],
                        "image_id": item["Image"],
                        "status": state["Status"],
                        "exit_code": state["ExitCode"],
                        "oom_killed": state["OOMKilled"],
                        "health": state.get("Health", {}).get("Status"),
                    }
                )
            networks = commands.run(
                [
                    "docker",
                    "network",
                    "ls",
                    "--quiet",
                    "--filter",
                    "label=com.docker.compose.project=" + project,
                ],
                env=env,
            ).split()
            result["networks"].extend(networks[:32])
        images = commands.run(["docker", "image", "ls", "--quiet", "--no-trunc"], env=env).split()
        result["images"] = []
        for identifier in sorted(set(images))[:32]:
            item = json.loads(commands.run(["docker", "image", "inspect", identifier], env=env))[0]
            result["images"].append(
                {
                    "id": item["Id"],
                    "architecture": item["Architecture"],
                    "path": image_path(item.get("Config", {}).get("Env") or [], private),
                }
            )
    except Exception as error:
        result["diagnostic_error_type"] = type(error).__name__
        result["diagnostic_command_failure"] = commands.last_failure
    evidence(root / "setup-diagnostics.json", result)
