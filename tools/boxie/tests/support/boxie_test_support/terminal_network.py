"""Production terminal hops with explicit in-process admission and daemon test boundaries.

This fixture proves terminal transport, not PostgreSQL/Temporal orchestration.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import httpx
from boxie.api.app import ApiConfig, create_app
from boxie.domain.errors import BoxieError
from boxie.domain.lifecycle import EnsureRequest, SandboxRef
from boxie.domain.models import Principal, SandboxSpec, SandboxView, TerminationReason
from boxie.sandbox.durable_client import DurableOrchestratorClient
from boxie.sandbox.durable_server import create_durable_orchestrator_app
from boxie.sandbox.lifecycle import DurableOrchestrator
from boxie.sandbox.reporting import ObservationReporter

from boxie_test_support.api import CONSUMER_AUTH, serve
from boxie_test_support.artifacts import Artifacts
from boxie_test_support.durable import Daemon, Guard
from boxie_test_support.orchestrator import Clock, configuration


class EchoDaemon(Daemon):
    @property
    def terminals(self) -> Any:
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
        await self.orchestrator.ensure(request)
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


@asynccontextmanager
async def network(root: Path, *, shell: str = "/bin/sh") -> AsyncIterator[Any]:
    import json

    config = configuration(root)
    profiles = json.loads(config.profiles_file.read_text())
    profiles["shell"]["commands"] = [[shell]]
    config.profiles_file.write_text(json.dumps(profiles))
    clock, daemon = Clock(), EchoDaemon()
    clock.now = datetime.now(UTC)
    artifacts = Artifacts(config.data_dir / "artifacts")

    async def path(principal: Principal, identifier: UUID) -> Path:
        return artifacts.path(principal, identifier)

    async def noop() -> None:
        pass

    orchestrator = DurableOrchestrator(config, daemon, path, Guard(), clock, clock.monotonic)
    # No artifact HTTP route is called on the orchestrator in this terminal-only harness.
    orchestrator_artifacts: Any = SimpleNamespace(collect=noop, collect_orphans=noop)
    app = create_durable_orchestrator_app(
        orchestrator,
        orchestrator_artifacts,
        credential=b"orchestrator-test-secret",
        reporter=ObservationReporter("http://127.0.0.1:1", b"unused"),
    )
    async with serve(app) as origin:
        async with DurableOrchestratorClient(
            origin, b"orchestrator-test-secret", timeout=1
        ) as client:
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
                    yield SimpleNamespace(
                        api=http,
                        orchestrator_url=origin,
                        daemon=daemon,
                        orchestrator=orchestrator,
                        config=config,
                        clock=clock,
                    )


async def attach(
    socket: Any, *, cursor: int = 0, cols: int = 80, rows: int = 24, order: int | None = None
) -> tuple[int, bytes]:
    import json

    request = {"type": "attach", "resume_after": cursor, "cols": cols, "rows": rows}
    if order is not None:
        request["attachment_order"] = order
    await socket.send(json.dumps(request))
    reply = json.loads(await socket.recv())
    assert reply["type"] == "attached", reply
    epoch = reply["epoch"]
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
