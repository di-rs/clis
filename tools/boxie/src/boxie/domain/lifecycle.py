"""Durable ownership and observation facts shared by agent and orchestration."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from boxie.domain.models import (
    AgentIdentity,
    Principal,
    ProfileSnapshot,
    SandboxSpec,
    SandboxState,
    TerminationReason,
)


@dataclass(frozen=True)
class DurableRequest:
    """Sensitive repository result. Never serialize this into workflow history."""

    sandbox_id: UUID
    principal: Principal
    identity: AgentIdentity
    generation: int
    agent_boot_id: UUID | None
    spec: SandboxSpec
    snapshot: ProfileSnapshot
    accepted_at: datetime
    absolute_deadline: datetime
    desired_stop: bool
    reason: TerminationReason | None


@dataclass(frozen=True)
class AgentObservation:
    """Accepted only from the authenticated agent bound to this ownership fence.

    Boot changes require an explicit compare-and-set bind_agent_boot call by a
    trusted control-plane caller; a report cannot select its own current boot.
    removal_confirmed means a successful physical absence check, never merely a
    terminal state or an unavailable runtime. Versions are monotonic per boot.
    """

    sandbox_id: UUID
    principal: Principal
    identity: AgentIdentity
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
