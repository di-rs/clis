"""Production terminal hops with explicit in-process admission and daemon test boundaries.

This fixture proves terminal transport, not PostgreSQL/Temporal orchestration.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from boxie.api.app import ApiConfig, create_app
from boxie.bootstrap.config import SupervisorConfig
from boxie.domain.errors import BoxieError
from boxie.domain.lifecycle import EnsureRequest, SandboxRef
from boxie.domain.models import Principal, SandboxSpec, SandboxView, TerminationReason
from boxie.sandbox.execution.supervisor import Supervisor
from boxie.sandbox.transport.client import SupervisorClient
from boxie.sandbox.transport.server import create_supervisor_app
from fastapi import FastAPI
from websockets.asyncio.client import ClientConnection

from boxie_test_support.api import CONSUMER_AUTH, serve
from boxie_test_support.artifacts import Artifacts
from boxie_test_support.execution import Daemon, Guard, launch_execution
from boxie_test_support.supervisor import Clock, FakeTerminal, configuration


class EchoDaemon(Daemon):
    @property
    def terminals(self) -> dict[str, FakeTerminal]:
        return self.streams

    async def start(self, identifier: str) -> None:
        await super().start(identifier)
        self.streams[identifier].queue.put_nowait(b"ready\n")


class Admission:
    def __init__(self, supervisor: Supervisor, artifacts: Artifacts) -> None:
        self.supervisor, self.artifacts = supervisor, artifacts
        self.keys: dict[tuple[Principal, str], UUID] = {}

    async def ready(self) -> bool:
        return True

    async def submit(self, principal: Principal, key: str, spec: SandboxSpec) -> SandboxView:
        if (principal, key) in self.keys:
            return await self.get(principal, self.keys[principal, key])
        identifier = uuid4()
        self.artifacts.path(principal, spec.artifact_id)
        request = EnsureRequest(
            SandboxRef(identifier, 1),
            principal,
            self.supervisor.config.identity,
            spec,
            self.supervisor.profiles.resolve_profile(spec.profile_id, spec.command, spec.env),
            self.supervisor.clock(),
            self.supervisor.clock() + timedelta(seconds=spec.policy.max_seconds),
        )
        await launch_execution(self.supervisor, request)
        self.keys[principal, key] = identifier
        return await self.get(principal, identifier)

    async def get(self, principal: Principal, identifier: UUID) -> SandboxView:
        record = self.supervisor.records.get(identifier)
        if record is None or record.consumer_id != principal.consumer_id:
            raise BoxieError(404, "sandbox not found")
        item = await self.supervisor.inspect(record.ref)
        return SandboxView(
            identifier,
            item.state,
            record.accepted_at,
            record.absolute_deadline,
            item.reason,
            item.cleanup_pending,
        )

    async def terminate(
        self, principal: Principal, identifier: UUID, reason: TerminationReason
    ) -> SandboxView:
        await self.get(principal, identifier)
        await self.supervisor.remove(self.supervisor.records[identifier].ref, reason)
        return await self.get(principal, identifier)


@dataclass
class Network:
    api: httpx.AsyncClient
    supervisor_url: str
    daemon: EchoDaemon
    supervisor: Supervisor
    config: SupervisorConfig
    clock: Clock


@asynccontextmanager
async def network(root: Path, *, daemon: EchoDaemon | None = None) -> AsyncIterator[Network]:
    config = configuration(root)
    clock = Clock()
    daemon = daemon or EchoDaemon()
    clock.now = datetime.now(UTC)
    artifacts = Artifacts(config.data_dir / "artifacts")

    async def path(principal: Principal, identifier: UUID) -> Path:
        return artifacts.path(principal, identifier)

    supervisor = Supervisor(config, daemon, path, Guard(), clock, clock.monotonic)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            await supervisor.start()
            yield
        finally:
            await supervisor.close()

    async def readiness() -> dict[str, bool]:
        return {"docker": await daemon.ready(), "execution": supervisor.execution_ready}

    app = create_supervisor_app(
        supervisor,
        artifacts,
        credential=b"supervisor-test-secret",
        readiness=readiness,
        lifespan=lifespan,
    )
    async with serve(app) as origin:
        async with SupervisorClient(origin, b"supervisor-test-secret", timeout=1) as client:
            api = create_app(
                ApiConfig(
                    release="terminal-test",
                    consumers={"one": b"consumer-one-secret", "two": b"consumer-two-secret"},
                ),
                Admission(supervisor, artifacts),
                artifacts,
                client,
            )
            async with serve(api) as url:
                async with httpx.AsyncClient(base_url=url, headers=CONSUMER_AUTH) as http:
                    yield Network(
                        api=http,
                        supervisor_url=origin,
                        daemon=daemon,
                        supervisor=supervisor,
                        config=config,
                        clock=clock,
                    )


async def attach(
    socket: ClientConnection,
    *,
    cursor: int = 0,
    cols: int = 80,
    rows: int = 24,
    order: int | None = None,
) -> tuple[int, bytes]:
    import json

    request = {"type": "attach", "resume_after": cursor, "cols": cols, "rows": rows}
    if order is not None:
        request["attachment_order"] = order
    await socket.send(json.dumps(request))
    reply = json.loads(await socket.recv())
    assert reply["type"] == "attached", reply
    epoch = reply["epoch"]
    assert isinstance(epoch, int)
    payload = bytearray()
    while True:
        frame = await socket.recv()
        if isinstance(frame, bytes):
            payload.extend(frame[8:])
            await socket.send(
                json.dumps(
                    {"type": "ack", "epoch": epoch, "sequence": int.from_bytes(frame[:8], "big")}
                )
            )
        elif json.loads(frame)["type"] == "geometry":
            pass
        else:
            assert json.loads(frame)["type"] == "replay_complete"
            break

    await socket.send(json.dumps({"type": "resize", "epoch": epoch, "cols": cols, "rows": rows}))
    assert json.loads(await socket.recv())["type"] == "resized"
    return epoch, bytes(payload)
