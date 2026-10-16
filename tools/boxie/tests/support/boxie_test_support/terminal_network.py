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
from boxie.bootstrap.config import OrchestratorConfig
from boxie.domain.errors import BoxieError
from boxie.domain.lifecycle import EnsureRequest, SandboxRef
from boxie.domain.models import Principal, SandboxSpec, SandboxView, TerminationReason
from boxie.sandbox.execution.orchestrator import DurableOrchestrator
from boxie.sandbox.transport.client import OrchestratorClient
from boxie.sandbox.transport.server import create_orchestrator_app
from fastapi import FastAPI
from websockets.asyncio.client import ClientConnection

from boxie_test_support.api import CONSUMER_AUTH, serve
from boxie_test_support.artifacts import Artifacts
from boxie_test_support.durable import Daemon, Guard, launch_execution
from boxie_test_support.orchestrator import Clock, FakeTerminal, configuration


class EchoDaemon(Daemon):
    @property
    def terminals(self) -> dict[str, FakeTerminal]:
        return self.streams

    async def start(self, identifier: str) -> None:
        await super().start(identifier)
        self.streams[identifier].queue.put_nowait(b"ready\n")


class Admission:
    def __init__(self, orchestrator: DurableOrchestrator, artifacts: Artifacts) -> None:
        self.orchestrator, self.artifacts = orchestrator, artifacts
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
            self.orchestrator.config.identity,
            spec,
            self.orchestrator.profiles.resolve_profile(spec.profile_id, spec.command, spec.env),
            self.orchestrator.clock(),
            self.orchestrator.clock() + timedelta(seconds=spec.policy.max_seconds),
        )
        await launch_execution(self.orchestrator, request)
        self.keys[principal, key] = identifier
        return await self.get(principal, identifier)

    async def get(self, principal: Principal, identifier: UUID) -> SandboxView:
        record = self.orchestrator.records.get(identifier)
        if record is None or record.consumer_id != principal.consumer_id:
            raise BoxieError(404, "sandbox not found")
        item = await self.orchestrator.inspect(record.ref)
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
        await self.orchestrator.remove(self.orchestrator.records[identifier].ref, reason)
        return await self.get(principal, identifier)


@dataclass
class Network:
    api: httpx.AsyncClient
    orchestrator_url: str
    daemon: EchoDaemon
    orchestrator: DurableOrchestrator
    config: OrchestratorConfig
    clock: Clock


@asynccontextmanager
async def network(
    root: Path, *, shell: str = "/bin/sh", daemon: EchoDaemon | None = None
) -> AsyncIterator[Network]:
    import json

    config = configuration(root)
    profiles = json.loads(config.profiles_file.read_text())
    profiles["shell"]["commands"] = [[shell]]
    config.profiles_file.write_text(json.dumps(profiles))
    clock = Clock()
    daemon = daemon or EchoDaemon()
    clock.now = datetime.now(UTC)
    artifacts = Artifacts(config.data_dir / "artifacts")

    async def path(principal: Principal, identifier: UUID) -> Path:
        return artifacts.path(principal, identifier)

    orchestrator = DurableOrchestrator(config, daemon, path, Guard(), clock, clock.monotonic)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            await orchestrator.start()
            yield
        finally:
            await orchestrator.close()

    async def readiness() -> dict[str, bool]:
        return {"docker": await daemon.ready(), "execution": orchestrator.execution_ready}

    app = create_orchestrator_app(
        orchestrator,
        artifacts,
        credential=b"orchestrator-test-secret",
        readiness=readiness,
        lifespan=lifespan,
    )
    async with serve(app) as origin:
        async with OrchestratorClient(origin, b"orchestrator-test-secret", timeout=1) as client:
            api = create_app(
                ApiConfig(
                    "terminal-test", {"one": b"consumer-one-secret", "two": b"consumer-two-secret"}
                ),
                Admission(orchestrator, artifacts),
                artifacts,
                client,
            )
            async with serve(api) as url:
                async with httpx.AsyncClient(base_url=url, headers=CONSUMER_AUTH) as http:
                    yield Network(
                        api=http,
                        orchestrator_url=origin,
                        daemon=daemon,
                        orchestrator=orchestrator,
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
