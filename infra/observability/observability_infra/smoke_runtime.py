"""Finite subprocesses and bounded, nonsecret evidence for the native smoke."""

import contextlib
import json
import os
import selectors
import signal
import subprocess
import time
from pathlib import Path
from typing import Any


class Deadline:
    def __init__(self, seconds: float, *, reserve: float = 30) -> None:
        if not 0 < reserve < seconds:
            raise ValueError("deadline must include a positive restoration reserve")
        self.started = time.monotonic()
        self.end = self.started + seconds
        self.reserve = reserve

    def remaining(self, *, restoring: bool = False) -> float:
        value = self.end - time.monotonic() - (0 if restoring else self.reserve)
        if value <= 0:
            raise TimeoutError("smoke deadline")
        return value

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started


class Commands:
    """Capture a small result; never put command arguments/output in exceptions."""

    def __init__(self, deadline: Deadline, *, output_limit: int = 262144) -> None:
        self.deadline = deadline
        self.output_limit = output_limit
        self.restoring = False

    def run(self, args: list[str], *, env: dict[str, str] | None = None) -> str:
        restoring = self.restoring
        self.deadline.remaining(restoring=restoring)
        process = subprocess.Popen(
            args,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        assert process.stdout is not None
        output = bytearray()
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = self.deadline.remaining(restoring=restoring)
                    for key, _ in selector.select(min(remaining, 0.1)):
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fd)
                        else:
                            output.extend(chunk)
                            if len(output) > self.output_limit:
                                raise RuntimeError("smoke command output limit exceeded")
                process.wait(timeout=self.deadline.remaining(restoring=restoring))
            if process.returncode:
                raise RuntimeError(f"smoke command exit {process.returncode}")
            return output.decode("utf-8", errors="replace").strip()
        finally:
            # Kill the whole session, including any descendants holding the pipe.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=1)
            process.stdout.close()


def check_privacy(text: str, sentinels: tuple[str, ...]) -> None:
    decoded: list[str] = [text]
    for line in text.splitlines():
        try:
            decoded.append(json.dumps(json.loads(line), ensure_ascii=False))
        except ValueError, TypeError:
            continue
    if any(secret in item for secret in sentinels for item in decoded):
        raise RuntimeError("smoke privacy assertion failed")


def evidence(path: Path, value: Any) -> None:
    """Atomic bounded allowlisted observations, never arbitrary command dumps."""
    data = json.dumps(value, indent=2, sort_keys=True).encode()
    if len(data) > 262144:
        raise RuntimeError("smoke evidence limit exceeded")
    temporary = path.with_suffix(".pending")
    temporary.touch(mode=0o600, exist_ok=True)
    temporary.chmod(0o600)
    temporary.write_bytes(data)
    temporary.replace(path)


def event_identities(text: str, instance: str) -> set[str]:
    """A parser comparison helper; JSONL itself is never journal ingestion proof."""
    from uuid import UUID

    identities = set()
    for line in text.splitlines():
        event = json.loads(line)
        if event.get("instance_id") == instance:
            identities.add(str(UUID(event["operation_id"])))
    return identities
