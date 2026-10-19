"""External Docker and authorization test boundaries for supervisor execution."""

import asyncio
from typing import Any
from uuid import uuid4

from boxie_test_support.supervisor import FakeTerminal


class Daemon:
    def __init__(self) -> None:
        self.resources: dict[str, Any] = {}
        self.streams: dict[str, FakeTerminal] = {}
        self.deleted_ids: list[str] = []
        self.started: list[str] = []
        self.creates = 0
        self.unreachable = False
        self.lost_ack = False
        self.create_entered = asyncio.Event()
        self.create_gate: asyncio.Event | None = None

    async def inspect(self, name: str) -> Any:
        if self.unreachable:
            raise OSError("daemon unreachable")
        return self.resources.get(name) or next(
            (r for r in self.resources.values() if r.id == name), None
        )

    async def create(self, name: str, config: dict[str, Any]) -> None:
        from boxie.sandbox.docker import ContainerInfo

        self.creates += 1
        self.create_entered.set()
        if self.create_gate:
            await self.create_gate.wait()  # Materialize only AFTER a prior absence check.
        self.resources[name] = ContainerInfo(
            id=str(uuid4()), labels=config["Labels"], running=False
        )
        if self.lost_ack:
            raise OSError("lost acknowledgement")

    async def attach(self, identifier: str) -> FakeTerminal:
        stream = FakeTerminal()
        self.streams[identifier] = stream
        return stream

    async def start(self, identifier: str) -> None:
        assert identifier in self.streams
        self.started.append(identifier)

    async def delete(self, identifier: str) -> None:
        if self.unreachable:
            raise OSError("daemon unreachable")
        self.deleted_ids.append(identifier)
        self.resources = {k: v for k, v in self.resources.items() if v.id != identifier}

    async def ready(self) -> bool:
        return not self.unreachable

    async def close(self) -> None:
        pass


class Guard:
    stopped = False
    receipt = False
    missing = False

    async def initialization(self, request: Any, boot: Any) -> Any:
        from boxie.domain.models import TerminationReason

        return None if self.missing else TerminationReason.REQUESTED if self.stopped else "execute"

    async def terminal_receipt(self, request: Any) -> bool:
        return self.receipt

    async def run_start_if_allowed(self, request: Any, boot: Any, start: Any) -> bool:
        if self.stopped:
            return False
        await start()
        return True

    async def start_allowed(self, request: Any, boot: Any) -> bool:
        return not self.stopped


async def launch_execution(supervisor: Any, request: Any) -> Any:
    """Launch an activity-shaped call for tests that manipulate in-flight Docker I/O."""
    from boxie.domain.models import SandboxState

    observation = await supervisor.initialize(request)
    execution = supervisor.executions[request.ref.id]
    if execution.activity is None and observation.state not in {
        SandboxState.READY,
        SandboxState.TERMINATED,
        SandboxState.FAILED,
    }:
        execution.activity = asyncio.create_task(supervisor.ensure_ready(request))
    return observation
