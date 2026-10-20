"""Sandbox contracts and validation, independent of transport."""

from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from cli_playground.terminal_protocol import TerminalEvent


class BoxieUnavailable(RuntimeError):
    """A bounded private service call failed; credentials never enter public errors."""


class TerminalWriteClosed(BoxieUnavailable):
    """Boxie closed normally while its final response may still be buffered."""


SandboxState = Literal["accepted", "provisioning", "ready", "terminating", "terminated", "failed"]
TerminationReason = Literal[
    "requested",
    "expired",
    "idle",
    "disconnected",
    "output_limit",
    "exited",
    "provisioning_failed",
    "supervisor_restart",
    "supervisor_shutdown",
    "control_plane_lost",
]


class SandboxView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    state: SandboxState
    created_at: AwareDatetime
    absolute_deadline: AwareDatetime
    reason: TerminationReason | None
    cleanup_pending: bool


class ArtifactReply(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: UUID
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    retired: bool


class SandboxRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact_id: UUID


class TerminalPort(Protocol):
    async def receive(self) -> bytes | TerminalEvent | None: ...
    async def send(self, message: str) -> None: ...


class BoxieClient(Protocol):
    async def upload(self, identifier: UUID, digest: str, archive: Path) -> None: ...
    async def retire(self, identifier: UUID) -> None: ...
    async def create(self, key: str, spec: SandboxRequest) -> SandboxView: ...
    async def get(self, identifier: UUID, previous: SandboxView | None = None) -> SandboxView: ...
    async def terminate(
        self, identifier: UUID, previous: SandboxView | None = None
    ) -> SandboxView: ...
    def terminal(self, identifier: UUID) -> AbstractAsyncContextManager[TerminalPort]: ...
    async def close(self) -> None: ...


def validate_sandbox(
    view: SandboxView,
    previous: SandboxView | None = None,
    *,
    identifier: UUID | None = None,
) -> None:
    lifetime = (view.absolute_deadline - view.created_at).total_seconds()
    if (
        not 0 < lifetime <= 3600
        or (identifier is not None and view.id != identifier)
        or (
            previous is not None
            and (
                view.id != previous.id
                or view.created_at != previous.created_at
                or view.absolute_deadline != previous.absolute_deadline
            )
        )
    ):
        raise BoxieUnavailable("invalid sandbox response")
