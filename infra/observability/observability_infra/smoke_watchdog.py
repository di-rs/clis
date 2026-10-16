"""Independent Linux journal-resume watchdog; no dependency on Docker or journal."""

import argparse
import contextlib
import json
import os
import signal
import time
from pathlib import Path
from typing import Any

from observability_infra.smoke_runtime import Commands, Deadline, evidence
from observability_infra.smoke_units import unit_memory, unit_name


def process_identity(pid: int) -> str:
    # proc stat field 22; comm may contain spaces and closing parentheses.
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


def resume(pid: int, identity: str) -> bool:
    try:
        if process_identity(pid) != identity:
            return False
        os.kill(pid, signal.SIGCONT)
        return True
    except FileNotFoundError, ProcessLookupError:
        return False


def restore_once(manifest: Path, commands: Commands) -> dict[str, bool]:
    """Retry only the exact identities registered before the fault."""
    data = json.loads(manifest.read_text())
    results: dict[str, bool] = {}
    if "pid" in data:
        results["journal_resumed"] = resume(int(data["pid"]), str(data["start"]))
    if "fillers" in data:
        results["filler_removed"] = True
    for item in data.get("fillers", []):
        path = Path(item["path"])
        try:
            if path.parent not in (manifest.parent / "journal", manifest.parent / "vector"):
                raise ValueError("foreign filler path")
            if path.exists() and path.stat().st_ino == item["inode"]:
                path.unlink()
            results["filler_removed"] &= not path.exists()
        except OSError, ValueError:
            results["filler_removed"] = False
    if "sink" in data:
        results["sink_started"] = False
        try:
            env = dict(os.environ, DOCKER_HOST="unix://" + str(manifest.parent / "docker.sock"))
            info = json.loads(commands.run(["docker", "inspect", data["sink"]], env=env))[0]
            if (
                info["Id"] != data["sink"]
                or info["Config"]["Labels"].get("com.docker.compose.project")
                != manifest.parent.name + "-telemetry"
            ):
                raise ValueError("foreign sink identity")
            commands.run(["docker", "start", data["sink"]], env=env)
            results["sink_started"] = True
        except RuntimeError, TimeoutError, ValueError, OSError, KeyError:
            pass
    return results


def restoration_valid(data: dict[str, Any], result: dict[str, Any]) -> bool:
    expected = {"journal_resumed"} if "pid" in data else set()
    expected |= {"filler_removed"} if "fillers" in data else set()
    expected |= {"sink_started"} if "sink" in data else set()
    return (
        bool(expected)
        and set(result) == expected
        and all(value is True for value in result.values())
    )


def restore_registered(
    manifest: Path, commands: Commands, *, wait_seconds: float = 22
) -> dict[str, Any]:
    data = json.loads(manifest.read_text())
    manifest.with_suffix(".restore").touch()
    path = manifest.with_suffix(".result.json")
    until = time.monotonic() + min(wait_seconds, commands.deadline.remaining(restoring=True))
    while not path.exists() and time.monotonic() < until:
        time.sleep(0.1)
    previous = json.loads(path.read_text()) if path.exists() else None
    if isinstance(previous, dict) and restoration_valid(data, previous):
        return {"restored": True, "watchdog": previous, "fallback": None}
    fallback = restore_once(manifest, commands)
    return {
        "restored": restoration_valid(data, fallback),
        "watchdog": previous,
        "fallback": fallback,
    }


def watch(manifest: Path) -> None:
    data = json.loads(manifest.read_text())
    if "watchdog_unit" in data:
        unit = unit_name(manifest.parent, data["scenario"], watchdog=True)
        if data["watchdog_unit"] != unit:
            raise RuntimeError("foreign watchdog unit")
        evidence(
            manifest.with_suffix(".unit.json"),
            {
                "unit": unit,
                "pid": os.getpid(),
                "start": process_identity(os.getpid()),
                "memory": unit_memory(manifest.parent, data["scenario"], watchdog=True),
            },
        )
    end = float(data["restore_at"])
    trigger = manifest.with_suffix(".restore")
    while time.monotonic() < end and not trigger.exists():
        time.sleep(min(0.1, max(0, end - time.monotonic())))
    results = restore_once(manifest, Commands(Deadline(20, reserve=1)))
    evidence(manifest.with_suffix(".result.json"), results)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    # Terminating a scenario must wake restoration, not terminate the watchdog.
    with contextlib.suppress(ValueError):
        signal.signal(signal.SIGTERM, lambda *_: args.manifest.with_suffix(".restore").touch())
    watch(args.manifest)


if __name__ == "__main__":
    main()
