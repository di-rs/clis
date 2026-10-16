"""Real loopback HTTP server; only Docker's external protocol is substituted."""

import asyncio
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from boxie.bootstrap.config import AgentConfig
from boxie.domain.models import AgentIdentity

IMAGE = "example.org/shell@sha256:" + "1" * 64
AUTH = {"Authorization": "Bearer agent-test-secret"}


def bundle(files: dict[str, bytes] | None = None) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in (files or {"nested/hello.txt": b"ordinary text\n"}).items():
            archive.writestr(name, value)
    return out.getvalue()


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 15, tzinfo=UTC)
        self.elapsed = 0.0

    def __call__(self) -> datetime:
        return self.now

    def monotonic(self) -> float:
        return self.elapsed

    def advance_wall(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)

    def advance_monotonic(self, seconds: float) -> None:
        self.elapsed += seconds

    def advance(self, seconds: float) -> None:
        self.advance_wall(seconds)
        self.advance_monotonic(seconds)


class FakeTerminal:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.closed = False
        self.sizes: list[tuple[int, int]] = []

    async def read(self) -> bytes:
        return await self.queue.get()

    async def write(self, data: bytes) -> None:
        await asyncio.sleep(0)
        self.queue.put_nowait(data)

    async def resize(self, columns: int, rows: int) -> None:
        self.sizes.append((columns, rows))

    async def close(self) -> None:
        self.closed = True
        self.queue.put_nowait(b"")


def configuration(root: Path, **changes: Any) -> AgentConfig:
    root.mkdir(exist_ok=True)
    secret = root / "agent.secret"
    secret.write_text("agent-test-secret\n")
    secret.chmod(0o600)
    profile = root / "profiles.json"
    profile.write_text(
        json.dumps(
            {
                "shell": {
                    "image_digest": IMAGE,
                    "commands": [["/bin/sh"]],
                    "env": {"TERM": ["xterm-256color"], "GREETING": ["hello"]},
                    "artifact_target": "/opt/artifact",
                }
            }
        )
    )
    return AgentConfig(
        data_dir=root / "state",
        profiles_file=profile,
        credential_file=secret,
        identity=AgentIdentity(uuid4(), "host-1"),
        sweep_seconds=0.01,
        **changes,
    )


def request(artifact_id: str, key: str = "first", **extra: Any) -> dict[str, Any]:
    return {
        "consumer_id": "one",
        "idempotency_key": key,
        "spec": {
            "artifact_id": artifact_id,
            "profile_id": "shell",
            "command": ["/bin/sh"],
            "env": {"TERM": "xterm-256color"},
            "policy": {"max_seconds": 3600, "idle_seconds": 600, "disconnect_seconds": 120},
        },
        **extra,
    }
