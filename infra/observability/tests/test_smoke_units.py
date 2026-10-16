"""Owned scenario cgroups preserve finite memory and independent restoration."""

import json
import os
from pathlib import Path

import pytest


def test_resident_limit_checks_identity_cap_and_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from observability_infra import smoke_units as units

    root = Path("/var/tmp/boxie-observability-0123456789ab")
    proc, groups = tmp_path / "proc", tmp_path / "groups"
    monkeypatch.setattr(units, "PROC", proc)
    monkeypatch.setattr(units, "CGROUP", groups)
    pid = os.getpid()
    process = proc / str(pid)
    process.mkdir(parents=True)
    name = units.unit_name(root, "lifecycle-failure", watchdog=False)
    cgroup = "/system.slice/" + name
    (process / "cgroup").write_text("0::" + cgroup + "\n")
    base = groups / cgroup.lstrip("/")
    base.mkdir(parents=True)
    for key, value in {
        "memory.max": "671088640",
        "memory.swap.max": "0",
        "memory.current": "1234",
        "memory.events": "oom 0\noom_kill 0\n",
    }.items():
        (base / key).write_text(value)
    assert units.unit_memory(root, "lifecycle-failure", watchdog=False)["memory.max"] == "671088640"
    for key, value in (
        ("memory.max", "max"),
        ("memory.max", "805306368"),
        ("memory.swap.max", "1"),
    ):
        original = (base / key).read_text()
        (base / key).write_text(value)
        with pytest.raises(RuntimeError):
            units.unit_memory(root, "lifecycle-failure", watchdog=False)
        (base / key).write_text(original)
    (process / "cgroup").write_text("0::/system.slice/foreign.service\n")
    with pytest.raises(RuntimeError):
        units.unit_memory(root, "lifecycle-failure", watchdog=False)


def test_watchdog_is_a_separate_finite_service_without_stop_dependency() -> None:
    from observability_infra.smoke_units import transient_command, unit_name

    root = Path("/var/tmp/boxie-observability-0123456789ab")
    driver = unit_name(root, "journal-stopped", watchdog=False)
    watch = transient_command(root, "journal-stopped", ["python", "watch.py"], watchdog=True)
    assert "--unit=" + unit_name(root, "journal-stopped", watchdog=True) in watch
    assert "--property=MemoryMax=134217728" in watch
    assert "--property=MemorySwapMax=0" in watch
    assert "--property=RuntimeMaxSec=180" in watch
    assert "--property=KillMode=control-group" in watch
    assert "--property=StandardOutput=null" in watch
    assert not any("PartOf" in item or "BindsTo" in item or driver in item for item in watch)
    assert "--scope" not in watch


def test_cleanup_refuses_foreign_unit_and_checks_process_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from observability_infra import smoke_units as units
    from observability_infra.smoke_runtime import Commands, Deadline

    root = Path("/var/tmp/boxie-observability-0123456789ab")
    commands = Commands(Deadline(5, reserve=0.1))
    calls: list[list[str]] = []
    details = {
        "LoadState": "loaded",
        "Description": units.description(root),
        "MainPID": "0",
        "ControlGroup": "",
    }

    def run(args: list[str], **kwargs: object) -> str:
        calls.append(args)
        if "show" in args:
            return "\n".join(key + "=" + value for key, value in details.items())
        return ""

    monkeypatch.setattr(commands, "run", run)
    units.stop_units(root, commands, ("lifecycle-failure",))
    assert len([args for args in calls if "stop" in args]) == 2
    details["Description"] = "foreign"
    calls.clear()
    with pytest.raises(RuntimeError):
        units.stop_units(root, commands, ("lifecycle-failure",))
    assert not any("stop" in args for args in calls)
    details["Description"] = units.description(root)
    details["MainPID"] = "123"
    with pytest.raises(RuntimeError):
        units.stop_units(root, commands, ("lifecycle-failure",))


def test_failed_watchdog_launch_is_registered_and_cannot_create_a_second(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from observability_infra import smoke_scenarios as scenarios
    from observability_infra.smoke_runtime import Commands, Deadline
    from observability_infra.smoke_units import DRIVER_BYTES, WATCHDOG_BYTES

    scenario = object.__new__(scenarios.Scenario)
    scenario.root, scenario.name = tmp_path, "journal-stopped"
    scenario.manifest = tmp_path / "watch-journal-stopped.json"
    scenario.commands = Commands(Deadline(5, reserve=1))
    scenario.watch = None
    monkeypatch.setattr(scenarios, "unit_name", lambda *args, **kwargs: "owned-watch.service")
    monkeypatch.setattr(scenarios, "transient_command", lambda *args, **kwargs: ["launch"])

    def fail(args: list[str], **kwargs: object) -> str:
        data = json.loads(scenario.manifest.read_text())
        assert data["watchdog_unit"] == "owned-watch.service"
        raise RuntimeError("launch failed")

    monkeypatch.setattr(scenario.commands, "run", fail)
    with pytest.raises(RuntimeError, match="launch failed"):
        asyncio.run(scenario.arm({"pid": 123, "start": "456"}))
    with pytest.raises(RuntimeError, match="only one watchdog"):
        asyncio.run(scenario.arm({"pid": 123, "start": "456"}))
    assert DRIVER_BYTES + WATCHDOG_BYTES == 768 * 1024**2


def test_cleanup_withholds_resources_when_owned_unit_does_not_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace
    from unittest.mock import Mock

    from observability_infra import smoke

    (tmp_path / "ownership.json").write_text("{}")
    close = Mock()
    monkeypatch.setattr(smoke, "require_ephemeral_runner", lambda: None)
    monkeypatch.setattr(
        smoke, "Resources", lambda *args: SimpleNamespace(state={"mounts": {}}, close=close)
    )

    def fail(*args: object) -> None:
        raise RuntimeError("owned unit still running")

    monkeypatch.setattr(smoke, "stop_units", fail)
    with pytest.raises(RuntimeError, match="teardown withheld"):
        smoke.cleanup(tmp_path)
    close.assert_not_called()
    assert (
        "RuntimeError:scenario-unit-cleanup"
        in json.loads((tmp_path / "cleanup.json").read_text())["errors"]
    )
