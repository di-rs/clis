"""Application ports have no dependency on the chosen transports or runtimes."""

from collections.abc import AsyncIterable, Mapping
from datetime import datetime
from typing import Protocol
from uuid import UUID

from boxie.domain.models import (
    AgentIdentity,
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


class AgentControl(Protocol):
    def resolve_profile(
        self, profile_id: str, command: tuple[str, ...], env: Mapping[str, str]
    ) -> ProfileSnapshot: ...
    async def ensure(
        self,
        identity: AgentIdentity,
        sandbox_id: UUID,
        generation: int,
        snapshot: ProfileSnapshot,
        artifact_id: UUID,
        deadline: datetime,
    ) -> SandboxView: ...
    async def inspect(
        self, identity: AgentIdentity, sandbox_id: UUID, generation: int
    ) -> SandboxView: ...
    async def remove(
        self, identity: AgentIdentity, sandbox_id: UUID, generation: int, reason: TerminationReason
    ) -> SandboxView: ...
