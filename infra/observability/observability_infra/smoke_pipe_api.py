"""Smoke-only launcher; an unread pipe belongs to the actual production API child."""

import json
import math
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

CONTROL = Path("/fixture-control")


def validate_shutdown(active: dict[str, Any], result: dict[str, Any]) -> None:
    if (
        active.get("unread") is not True
        or type(active.get("pid")) is not int
        or active["pid"] <= 0
        or not isinstance(active.get("start"), str)
        or not active["start"].isdigit()
        or result.get("pid") != active["pid"]
        or result.get("start") != active["start"]
    ):
        raise RuntimeError("unread pipe child identity differs")
    seconds = result.get("seconds")
    # Uvicorn0.54 re-raises captured SIGTERM after graceful server shutdown.
    if (
        result.get("forced") is not False
        or type(result.get("exit")) is not int
        or result["exit"] not in (0, -signal.SIGTERM)
        or not isinstance(seconds, (int, float))
        or isinstance(seconds, bool)
        or not math.isfinite(seconds)
        or not 0 <= seconds <= 8
    ):
        raise RuntimeError("production API did not shut down while stdout unread")


def main() -> None:
    command = ["boxie-api", "--config", "/etc/boxie/api.json"]
    if not (CONTROL / "blocked-stdout").exists():
        os.execvp(command[0], command)
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert process.stdout is not None
    identity = Path(f"/proc/{process.pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    (CONTROL / "pipe-active.json").write_text(
        json.dumps({"pid": process.pid, "start": identity, "unread": True})
    )
    requested = False

    def stop(*_: object) -> None:
        nonlocal requested
        requested = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while process.poll() is None and not requested and not (CONTROL / "stop-api").exists():
        time.sleep(0.05)
    started = time.monotonic()
    process.terminate()
    try:
        code = process.wait(timeout=8)
        forced = False
    except subprocess.TimeoutExpired:
        process.kill()
        code = process.wait(timeout=1)
        forced = True
    # Pipe remains unread through the completed production shutdown.
    (CONTROL / "pipe-exit.json").write_text(
        json.dumps(
            {
                "pid": process.pid,
                "start": identity,
                "exit": code,
                "forced": forced,
                "seconds": time.monotonic() - started,
            }
        )
    )
    process.stdout.close()


if __name__ == "__main__":
    main()
