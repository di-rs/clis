"""Finite sibling units for the native scenario driver and restoration watchdog."""

import json
import os
import re
import sys
import time
from pathlib import Path

from observability_infra.smoke_runtime import Commands, evidence

PROC = Path("/proc")
CGROUP = Path("/sys/fs/cgroup")
DRIVER_BYTES = 640 * 1024**2
WATCHDOG_BYTES = 128 * 1024**2


def unit_name(root: Path, scenario: str, *, watchdog: bool) -> str:
    if not re.fullmatch(
        r"/var/tmp/boxie-observability-[a-f0-9]{12}", str(root)
    ) or not re.fullmatch(r"[a-z]+(?:-[a-z]+)*", scenario):
        raise ValueError("exact owned scenario identity required")
    return root.name + ("-watch-" if watchdog else "-driver-") + scenario + ".service"


def description(root: Path) -> str:
    return "Owned Boxie smoke " + root.name


def transient_command(
    root: Path, scenario: str, command: list[str], *, watchdog: bool, wait: bool = True
) -> list[str]:
    result = [
        "systemd-run",
        "--collect",
        "--service-type=exec",
        "--expand-environment=no",
        "--unit=" + unit_name(root, scenario, watchdog=watchdog),
        "--description=" + description(root),
        "--working-directory=" + str(Path.cwd()),
        "--property=MemoryMax=" + str(WATCHDOG_BYTES if watchdog else DRIVER_BYTES),
        "--property=MemorySwapMax=0",
        "--property=KillMode=control-group",
        "--property=RuntimeMaxSec=180",
        "--property=TimeoutStopSec=5",
        "--setenv=PATH=" + os.environ["PATH"],
        "--setenv=GITHUB_ACTIONS=" + os.environ.get("GITHUB_ACTIONS", ""),
        "--setenv=RUNNER_ENVIRONMENT=" + os.environ.get("RUNNER_ENVIRONMENT", ""),
    ]
    result += (
        ["--property=StandardOutput=null", "--property=StandardError=null"]
        if watchdog or not wait
        else ["--wait", "--pipe"]
    )
    return [*result, "--", *command]


def unit_memory(root: Path, scenario: str, *, watchdog: bool) -> dict[str, str]:
    expected = "/system.slice/" + unit_name(root, scenario, watchdog=watchdog)
    current = (PROC / str(os.getpid()) / "cgroup").read_text().strip()
    if current != "0::" + expected:
        raise RuntimeError("foreign scenario process cgroup")
    base = CGROUP / expected.lstrip("/")
    result = {
        name: (base / name).read_text().strip()
        for name in ("memory.current", "memory.max", "memory.swap.max", "memory.events")
    }
    if (
        result["memory.max"] != str(WATCHDOG_BYTES if watchdog else DRIVER_BYTES)
        or result["memory.swap.max"] != "0"
    ):
        raise RuntimeError("scenario resident memory budget differs")
    return {"cgroup": expected, **result}


def verify_empty(name: str) -> None:
    try:
        events = (CGROUP / "system.slice" / name / "cgroup.events").read_text()
    except FileNotFoundError:
        return
    if "populated 1" in events.splitlines():
        raise RuntimeError("owned scenario cgroup remains populated")


def stop_units(root: Path, commands: Commands, scenarios: tuple[str, ...]) -> None:
    """Called outside the driver, after restoration and before resource teardown."""
    for scenario in scenarios:
        for watchdog in (False, True):
            name = unit_name(root, scenario, watchdog=watchdog)
            args = [
                "systemctl",
                "show",
                name,
                "--property=LoadState,Description,MainPID,ControlGroup",
            ]
            state = dict(line.split("=", 1) for line in commands.run(args).splitlines())
            if state.get("LoadState") == "not-found":
                verify_empty(name)
                continue
            if state.get("Description") != description(root):
                raise RuntimeError("foreign scenario unit cleanup refused")
            commands.run(["systemctl", "stop", name])
            state = dict(line.split("=", 1) for line in commands.run(args).splitlines())
            if state.get("MainPID") not in ("", "0", None):
                raise RuntimeError("owned scenario process remains")
            verify_empty(name)


def probe_independence(root: Path, commands: Commands) -> None:
    """No-fault native prerequisite: sibling survives driver-unit termination."""
    scenario = "unit-probe"
    report = root / "unit-probe.json"
    evidence(
        report,
        {
            "status": "starting",
            "units": [unit_name(root, scenario, watchdog=flag) for flag in (False, True)],
        },
    )
    program = """
import json, os, sys, time
from pathlib import Path
from observability_infra.smoke_runtime import evidence
from observability_infra.smoke_units import unit_memory
from observability_infra.smoke_watchdog import process_identity
root = Path(sys.argv[1])
watchdog = sys.argv[2] == 'watch'
role = 'watch' if watchdog else 'driver'
identity = {'pid': os.getpid(), 'start': process_identity(os.getpid()), 'memory': unit_memory(root, 'unit-probe', watchdog=watchdog)}
evidence(root / ('probe-' + role + '.json'), identity)
if watchdog:
    driver = json.loads((root / 'probe-driver.json').read_text())
    end = time.monotonic() + 10
    while time.monotonic() < end:
        try:
            alive = process_identity(driver['pid']) == driver['start']
        except FileNotFoundError:
            alive = False
        if not alive:
            evidence(root / 'probe-survived.json', identity)
            break
        time.sleep(.05)
    else:
        raise RuntimeError('probe driver did not exit')
else:
    time.sleep(15)
"""

    def await_file(path: Path) -> dict[str, object]:
        end = time.monotonic() + min(12, commands.deadline.remaining())
        while not path.exists():
            if time.monotonic() >= end:
                raise RuntimeError("owned unit probe timed out")
            time.sleep(0.05)
        return dict(json.loads(path.read_text()))

    try:
        for watchdog in (False, True):
            role = "watch" if watchdog else "driver"
            commands.run(
                transient_command(
                    root,
                    scenario,
                    [sys.executable, "-c", program, str(root), role],
                    watchdog=watchdog,
                    wait=False,
                )
            )
            await_file(root / ("probe-" + role + ".json"))
        driver = unit_name(root, scenario, watchdog=False)
        commands.run(["systemctl", "stop", driver])
        survived = await_file(root / "probe-survived.json")
        started = await_file(root / "probe-watch.json")
        if survived != started:
            raise RuntimeError("watchdog identity changed across driver termination")
        stop_units(root, commands, (scenario,))
        evidence(
            report,
            {
                "status": "passed",
                "driver": await_file(root / "probe-driver.json"),
                "watchdog": survived,
                "driver_stopped": True,
                "watchdog_survived": True,
                "units_exited": True,
            },
        )
    finally:
        stop_units(root, commands, (scenario,))
