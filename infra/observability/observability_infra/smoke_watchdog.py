"""Independent Linux journal-resume watchdog; no dependency on Docker or journal."""

import argparse
import contextlib
import json
import os
import signal
import time
from pathlib import Path

from observability_infra.smoke_runtime import Commands, Deadline, evidence


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


def watch(manifest: Path) -> None:
    data = json.loads(manifest.read_text())
    # Manifest contains one exact selected journal process and deadline only.
    end = float(data["restore_at"])
    trigger = manifest.with_suffix(".restore")
    while time.monotonic() < end and not trigger.exists():
        time.sleep(min(0.1, max(0, end - time.monotonic())))
    results: dict[str, bool] = {}
    if "pid" in data:
        results["journal_resumed"] = resume(int(data["pid"]), str(data["start"]))
    for item in data.get("fillers", []):
        path = Path(item["path"])
        if path.parent not in (manifest.parent / "journal", manifest.parent / "vector"):
            raise ValueError("foreign filler path")
        if path.exists() and path.stat().st_ino == item["inode"]:
            path.unlink()
        results["filler_removed"] = not path.exists()
    if "sink" in data:
        commands = Commands(Deadline(20, reserve=1))
        env = dict(os.environ, DOCKER_HOST="unix://" + str(manifest.parent / "docker.sock"))
        info = json.loads(commands.run(["docker", "inspect", data["sink"]], env=env))[0]
        if (
            info["Config"]["Labels"].get("com.docker.compose.project")
            != manifest.parent.name + "-telemetry"
        ):
            raise ValueError("foreign sink identity")
        commands.run(["docker", "start", data["sink"]], env=env)
        results["sink_started"] = True
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
