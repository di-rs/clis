"""Product test double for the independently deployed service port."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from playground.boxie_client import SandboxRequest, SandboxView, TerminalPort


class EchoTerminal:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.queue.put_nowait(b"ready\n")

    async def receive(self) -> bytes:
        return await self.queue.get()

    async def send(self, message: str) -> None:
        import json

        data = json.loads(message)
        await asyncio.sleep(0)
        if data["type"] == "input":
            self.queue.put_nowait(data["data"].encode())


class FakeBoxie:
    def __init__(self) -> None:
        self.artifacts: dict[UUID, bytes] = {}
        self.retired: set[UUID] = set()
        self.sandboxes: dict[UUID, SandboxView] = {}
        self.requests: dict[str, UUID] = {}
        self.attachments = 0

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
        now = datetime.now(UTC)
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

    async def get(self, identifier: UUID) -> SandboxView:
        return self.sandboxes[identifier]

    async def terminate(self, identifier: UUID) -> SandboxView:
        result = self.sandboxes[identifier].model_copy(
            update={"state": "terminated", "reason": "requested"}
        )
        self.sandboxes[identifier] = result
        return result

    @asynccontextmanager
    async def terminal(self, identifier: UUID) -> AsyncIterator[TerminalPort]:
        self.attachments += 1
        yield EchoTerminal()

    async def close(self) -> None:
        pass
