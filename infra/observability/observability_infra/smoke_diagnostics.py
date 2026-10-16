"""Bounded setup-failure observations before owned Compose teardown."""

import json
import re
from pathlib import Path
from typing import Any

from observability_infra.smoke_runtime import Commands, Deadline, evidence


def setup_diagnostics(root: Path, stack: Any, failed: dict[str, Any] | None) -> None:
    commands = Commands(Deadline(15, reserve=0.1))
    result: dict[str, Any] = {"failed_command": failed, "containers": [], "networks": []}
    env = stack.env
    try:
        info = json.loads(commands.run(["docker", "info", "--format", "{{json .}}"], env=env))
        if (
            info["DockerRootDir"] != str(root / "docker-data")
            or "io.boxie.test.owner=" + root.name not in info["Labels"]
        ):
            raise RuntimeError("diagnostic daemon ownership mismatch")
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
        for project in (stack.platform, stack.temporal, stack.runtime):
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
                item = json.loads(commands.run(["docker", "inspect", identifier], env=env))[0]
                state = item["State"]
                result["containers"].append(
                    {
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
            result["images"].append({"id": item["Id"], "architecture": item["Architecture"]})
    except Exception as error:
        result["diagnostic_error_type"] = type(error).__name__
        result["diagnostic_command_failure"] = commands.last_failure
    evidence(root / "setup-diagnostics.json", result)
