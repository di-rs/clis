"""Product test double for the independently deployed service port."""

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from cli_playground.sandbox import SandboxRequest, SandboxState, SandboxView, TerminalPort
from cli_playground.terminal_protocol import TerminalEvent, response


class EchoTerminal:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes | str] = asyncio.Queue()
        self.sequence = 1

    async def receive(self) -> bytes | TerminalEvent:
        frame = await self.queue.get()
        return response.validate_json(frame) if isinstance(frame, str) else frame

    async def send(self, message: str) -> None:
        import json

        data = json.loads(message)
        await asyncio.sleep(0)
        if data["type"] == "attach":
            self.queue.put_nowait(json.dumps({"type": "attached", "epoch": 1}))
            self.queue.put_nowait((1).to_bytes(8, "big") + b"ready\n")
            self.queue.put_nowait(
                json.dumps({"type": "replay_complete", "epoch": 1, "sequence": 1})
            )
        elif data["type"] == "resize":
            self.queue.put_nowait(
                json.dumps(
                    {"type": "resized", "epoch": 1, "cols": data["cols"], "rows": data["rows"]}
                )
            )
        elif data["type"] == "input":
            self.sequence += 1
            self.queue.put_nowait(self.sequence.to_bytes(8, "big") + data["data"].encode())
        elif data["type"] == "heartbeat":
            self.queue.put_nowait(message)


class FakeBoxie:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        terminal_factory: Callable[[], TerminalPort] = EchoTerminal,
    ) -> None:
        self.clock = clock
        self.terminal_factory = terminal_factory
        self.artifacts: dict[UUID, bytes] = {}
        self.retired: set[UUID] = set()
        self.sandboxes: dict[UUID, SandboxView] = {}
        self.requests: dict[str, UUID] = {}
        self.attachments = 0
        self.stops: list[UUID] = []
        self.termination_state: SandboxState = "terminated"

    async def upload(self, identifier: UUID, digest: str, archive: Path) -> None:
        import hashlib

        data = archive.read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest
        assert identifier not in self.artifacts or self.artifacts[identifier] == data
        self.artifacts[identifier] = data

    async def retire(self, identifier: UUID) -> None:
        self.retired.add(identifier)

    async def create(self, key: str, spec: SandboxRequest) -> SandboxView:
        if key in self.requests:
            return self.sandboxes[self.requests[key]]
        assert spec.artifact_id in self.artifacts and spec.artifact_id not in self.retired
        now = datetime.fromtimestamp(self.clock(), UTC)
        result = SandboxView(
            id=uuid4(),
            state="ready",
            created_at=now,
            absolute_deadline=now + timedelta(hours=1),
            reason=None,
            cleanup_pending=False,
        )
        self.requests[key] = result.id
        self.sandboxes[result.id] = result
        return result

    async def get(self, identifier: UUID, previous: SandboxView | None = None) -> SandboxView:
        return self.sandboxes[identifier]

    async def terminate(self, identifier: UUID, previous: SandboxView | None = None) -> SandboxView:
        self.stops.append(identifier)
        result = self.sandboxes[identifier].model_copy(
            update={"state": self.termination_state, "reason": "requested"}
        )
        self.sandboxes[identifier] = result
        return result

    @asynccontextmanager
    async def terminal(self, identifier: UUID) -> AsyncIterator[TerminalPort]:
        self.attachments += 1
        yield self.terminal_factory()

    async def close(self) -> None:
        pass
