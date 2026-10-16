"""Application-facing network ports; API adapters own their wire presentation."""

from collections.abc import AsyncIterable
from contextlib import AbstractAsyncContextManager
from typing import Protocol
from uuid import UUID

from boxie.application.ports import ArtifactAccess, Lifecycle
from boxie.domain.models import ArtifactPutResult, Principal


class ReadyLifecycle(Lifecycle, Protocol):
    async def ready(self) -> bool: ...


class TerminalConnection(Protocol):
    async def receive(self) -> bytes | str: ...
    async def send(self, data: bytes | str) -> None: ...


class TerminalAccess(Protocol):
    def attach(
        self, principal: Principal, sandbox_id: UUID
    ) -> AbstractAsyncContextManager[TerminalConnection]: ...


class Service:
    def __init__(
        self, lifecycle: ReadyLifecycle, artifacts: ArtifactAccess, terminal: TerminalAccess
    ) -> None:
        self.lifecycle, self.artifacts, self.terminal = lifecycle, artifacts, terminal

    async def put(
        self, principal: Principal, identifier: UUID, digest: str, chunks: AsyncIterable[bytes]
    ) -> ArtifactPutResult:
        return await self.artifacts.put(principal, identifier, digest, chunks)
