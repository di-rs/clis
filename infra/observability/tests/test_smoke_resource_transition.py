"""A vanished process is unavailable only after a verified same-container transition."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from observability_infra.smoke_runtime import Commands, Deadline
from observability_infra.smoke_telemetry import Telemetry


@pytest.mark.parametrize(
    "change",
    [
        "pid",
        "restarting",
        "already_restarting",
        "same",
        "foreign",
        "permission",
        "oom",
        "other_service",
    ],
)
def test_missing_proc_requires_verified_vector_transition(
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    telemetry = object.__new__(Telemetry)
    telemetry.project, telemetry.env = "owned-telemetry", {}
    telemetry.commands = Commands(Deadline(5, reserve=1))
    item: dict[str, Any] = {
        "Id": "a" * 64,
        "Name": "/owned-vector",
        "RestartCount": 0,
        "Config": {
            "Labels": {
                "com.docker.compose.project": telemetry.project,
                "com.docker.compose.service": "vector",
            }
        },
        "HostConfig": {"LogConfig": {"Type": "journald", "Config": {}}},
        "State": {
            "Pid": 20,
            "StartedAt": "2026-10-16T00:00:00Z",
            "Running": True,
            "Restarting": False,
            "OOMKilled": False,
            "ExitCode": 0,
        },
    }
    if change == "already_restarting":
        item["State"].update(Pid=0, Restarting=True)
    if change == "other_service":
        item["Config"]["Labels"]["com.docker.compose.service"] = "prometheus"
    after = copy.deepcopy(item)
    if change == "pid":
        after["State"]["Pid"] = 30
        after["RestartCount"] = 1
    elif change == "restarting":
        after["State"]["Restarting"] = True
        after["State"]["Pid"] = 0
    elif change == "oom":
        after["State"].update(Pid=30, OOMKilled=True)
    elif change == "foreign":
        after["Id"] = "b" * 64
    calls = 0

    def run(args: list[str], **kwargs: Any) -> str:
        nonlocal calls
        calls += 1
        return json.dumps([item if calls == 1 else after])

    def read(path: Path, *args: Any, **kwargs: Any) -> str:
        assert str(path) == f"/proc/{item['State']['Pid']}/cgroup"
        if change == "permission":
            raise PermissionError
        raise FileNotFoundError

    monkeypatch.setattr(telemetry.commands, "run", run)
    monkeypatch.setattr(telemetry, "compose", lambda *args: "a" * 64)
    monkeypatch.setattr(Path, "read_text", read)
    if change in ("same", "foreign", "permission", "oom", "other_service"):
        with pytest.raises((FileNotFoundError, RuntimeError, PermissionError)):
            telemetry.container_sample("a" * 12)
    else:
        result = telemetry.container_sample("a" * 12)
        assert result["memory"] is None
        assert result["transition"]["before"]["Pid"] == item["State"]["Pid"]
        assert result["transition"]["after"]["Id"] == item["Id"]
