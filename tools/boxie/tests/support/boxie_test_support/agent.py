"""Real loopback HTTP server; only Docker's external protocol is substituted."""

import asyncio
import hashlib
import io
import json
import socket
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import uvicorn
from boxie.bootstrap.config import AgentConfig, read_credential
from boxie.domain.models import AgentIdentity
from boxie.persistence.artifacts import ArtifactStore
from boxie.sandbox.server import create_agent_app

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

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


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


class DockerDaemon:
    def __init__(self) -> None:
        self.configs: dict[str, dict[str, Any]] = {}
        self.terminals: dict[str, FakeTerminal] = {}
        self.removed: list[str] = []
        self.fail_delete = False
        self.fail_start = False
        self.create_gate: asyncio.Event | None = None
        self.created = asyncio.Event()
        self.events: list[str] = []

    async def ready(self) -> bool:
        return True

    async def create(self, name: str, config: dict[str, Any]) -> None:
        assert name not in self.configs
        self.configs[name] = config
        self.events.append("create")
        self.created.set()
        if self.create_gate is not None:
            await self.create_gate.wait()

    async def attach(self, name: str) -> FakeTerminal:
        self.events.append("attach")
        terminal = FakeTerminal()
        self.terminals[name] = terminal
        return terminal

    async def start(self, name: str) -> None:
        assert name in self.terminals, "output lost: attach must precede start"
        self.events.append("start")
        if self.fail_start:
            raise RuntimeError("daemon unavailable")
        self.terminals[name].queue.put_nowait(b"ready\n")

    async def labels(self, name: str) -> dict[str, str] | None:
        return self.configs[name]["Labels"] if name in self.configs else None

    async def delete(self, name: str) -> None:
        if self.fail_delete:
            raise RuntimeError("daemon unavailable")
        self.configs.pop(name, None)
        self.removed.append(name)

    async def close(self) -> None:
        pass


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


@asynccontextmanager
async def live(
    config: AgentConfig, daemon: DockerDaemon, clock: Clock
) -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:
    store = ArtifactStore(config.data_dir / "artifacts")
    app = create_agent_app(
        config, daemon, store, clock, credential=read_credential(config.credential_file)
    )
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", ws="websockets-sansio"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        for _ in range(200):
            if server.started:
                break
            if task.done():
                await task
                raise AssertionError("server did not start")
            await asyncio.sleep(0.01)
        assert server.started
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=AUTH) as client:
            yield client, app.state.agent
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        sock.close()


async def upload(client: httpx.AsyncClient, data: bytes | None = None) -> str:
    content = data or bundle()
    identifier = str(uuid4())
    result = await client.put(
        f"/v1/artifacts/{identifier}",
        params={"consumer_id": "one"},
        headers={"X-Content-SHA256": hashlib.sha256(content).hexdigest()},
        content=content,
    )
    assert result.status_code == 201, result.text
    return identifier


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


async def settled(client: httpx.AsyncClient, identifier: str, state: str) -> dict[str, Any]:
    for _ in range(200):
        response = await client.get(f"/v1/sandboxes/{identifier}", params={"consumer_id": "one"})
        assert response.status_code == 200, response.text
        result: dict[str, Any] = response.json()
        if result["state"] == state:
            return result
        await asyncio.sleep(0.01)
    raise AssertionError(result)
