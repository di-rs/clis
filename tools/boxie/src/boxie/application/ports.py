"""Application ports have no dependency on the chosen transports or runtimes."""

from collections.abc import AsyncIterable
from contextlib import AbstractAsyncContextManager
from typing import Protocol
from uuid import UUID

from boxie.contracts.terminal import TerminalEvent
from boxie.domain.models import (
    ArtifactPutResult,
    Principal,
    ProfileSnapshot,
    SandboxSpec,
    SandboxView,
    TerminationReason,
)


class Lifecycle(Protocol):
    async def submit(
        self, principal: Principal, idempotency_key: str, spec: SandboxSpec
    ) -> SandboxView: ...
    async def get(self, principal: Principal, sandbox_id: UUID) -> SandboxView: ...
    async def terminate(
        self, principal: Principal, sandbox_id: UUID, reason: TerminationReason
    ) -> SandboxView: ...


class ArtifactAccess(Protocol):
    async def put(
        self, principal: Principal, artifact_id: UUID, sha256: str, chunks: AsyncIterable[bytes]
    ) -> ArtifactPutResult: ...
    async def retire(self, principal: Principal, artifact_id: UUID) -> None: ...


class ReadyLifecycle(Lifecycle, Protocol):
    async def ready(self) -> bool: ...


class TerminalConnection(Protocol):
    async def receive(self) -> bytes | TerminalEvent: ...
    async def send(self, data: bytes | str) -> None: ...


class TerminalAccess(Protocol):
    def attach(
        self, principal: Principal, sandbox_id: UUID
    ) -> AbstractAsyncContextManager[TerminalConnection]: ...


class AdmissionProfileResolver(Protocol):
    async def resolve_admission(self, spec: SandboxSpec) -> ProfileSnapshot: ...
