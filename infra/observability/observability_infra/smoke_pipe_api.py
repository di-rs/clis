"""Smoke-only launcher; an unread pipe belongs to the actual production API child."""

import json
import os
import signal
import subprocess
import time
from pathlib import Path

CONTROL = Path("/fixture-control")


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
