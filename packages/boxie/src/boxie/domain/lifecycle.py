"""Sandbox ownership and observation facts shared by orchestrator and orchestration."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from boxie.domain.models import (
    OrchestratorIdentity,
    Principal,
    ProfileSnapshot,
    SandboxSpec,
    SandboxState,
    TerminationReason,
)


@dataclass(frozen=True)
class SandboxRequest:
    """Sensitive repository result. Never serialize this into workflow history."""

    sandbox_id: UUID
    principal: Principal
    identity: OrchestratorIdentity
    generation: int
    orchestrator_boot_id: UUID | None
    spec: SandboxSpec
    snapshot: ProfileSnapshot
    accepted_at: datetime
    absolute_deadline: datetime
    desired_stop: bool
    reason: TerminationReason | None


@dataclass(frozen=True)
class OrchestratorObservation:
    """Accepted only from the authenticated orchestrator bound to this ownership fence.

    Boot changes require an explicit compare-and-set bind_orchestrator_boot call by a
    trusted control-plane caller; a report cannot select its own current boot.
    removal_confirmed means a successful physical absence check, never merely a
    terminal state or unavailable sandbox execution. Versions are monotonic per boot.
    """

    sandbox_id: UUID
    principal: Principal
    identity: OrchestratorIdentity
    generation: int
    boot_id: UUID
    event_version: int
    observed_at: datetime
    state: SandboxState
    resource_present: bool
    removal_confirmed: bool = False
    cleanup_pending: bool = False
    reason: TerminationReason | None = None
    ready_at: datetime | None = None
    last_input_at: datetime | None = None
    disconnected_at: datetime | None = None

    attachment_epoch: int = 0
    absolute_deadline: datetime | None = None
    control_lease_until: datetime | None = None
    container_id: str | None = None

    @property
    def ref(self) -> SandboxRef:
        return SandboxRef(self.sandbox_id, self.generation)

    def __post_init__(self) -> None:
        if self.generation < 1 or self.event_version < 1:
            raise ValueError("positive generation and event version required")
        for value in (self.observed_at, self.ready_at, self.last_input_at, self.disconnected_at):
            if value is not None and value.tzinfo is None:
                raise ValueError("observation times require timezones")
        if self.removal_confirmed and (
            self.cleanup_pending
            or self.resource_present
            or self.state not in (SandboxState.TERMINATED, SandboxState.FAILED)
        ):
            raise ValueError("confirmed removal requires absent resource and terminal state")


@dataclass(frozen=True)
class SandboxRef:
    id: UUID
    generation: int

    def __post_init__(self) -> None:
        if self.generation < 1:
            raise ValueError("positive generation required")


@dataclass(frozen=True)
class EnsureRequest:
    ref: SandboxRef
    principal: Principal
    identity: OrchestratorIdentity
    spec: SandboxSpec
    profile_snapshot: ProfileSnapshot
    accepted_at: datetime
    absolute_deadline: datetime

    @classmethod
    def from_request(cls, request: SandboxRequest) -> EnsureRequest:
        return cls(
            SandboxRef(request.sandbox_id, request.generation),
            request.principal,
            request.identity,
            request.spec,
            request.snapshot,
            request.accepted_at,
            request.absolute_deadline,
        )


class StartGuard(Protocol):
    """Orchestrator-owned guard. The callback is only the bounded final Docker start.

    start_allowed validates immutable admission AND its retained artifact reference,
    before mount and again before READY. It is not final-start authorization.
    """

    async def initialization(
        self, request: EnsureRequest, boot_id: UUID
    ) -> Literal["execute"] | TerminationReason | None: ...

    async def terminal_receipt(self, request: EnsureRequest) -> bool: ...

    async def start_allowed(self, request: EnsureRequest, boot_id: UUID) -> bool: ...
    async def run_start_if_allowed(
        self, request: EnsureRequest, boot_id: UUID, start: Callable[[], Awaitable[None]]
    ) -> bool: ...
