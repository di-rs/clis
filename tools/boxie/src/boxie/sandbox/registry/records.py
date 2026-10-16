"""Persisted sandbox ownership records and process-local deadline values."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import Field

from boxie.domain.models import (
    Principal,
    SandboxState,
    SandboxView,
    TerminationReason,
)
from boxie.sandbox.protocol import IdentityWire, SnapshotWire, SpecWire, WireModel

FINAL = {SandboxState.TERMINATED, SandboxState.FAILED}


class Record(WireModel):
    id: UUID
    consumer_id: str
    idempotency_key: str
    fingerprint: str
    spec: SpecWire
    snapshot: SnapshotWire
    identity: IdentityWire
    generation: int = 1
    created_at: datetime
    absolute_deadline: datetime
    idle_deadline: datetime | None = None
    disconnected_at: datetime | None = None
    state: SandboxState = SandboxState.ACCEPTED
    reason: TerminationReason | None = None
    cleanup_pending: bool = False
    creation_uncertain: bool = False
    output_bytes: int = 0

    def view(self) -> SandboxView:
        return SandboxView(
            self.id,
            self.state,
            self.created_at,
            self.absolute_deadline,
            self.reason,
            self.cleanup_pending,
        )

    @property
    def name(self) -> str:
        return f"boxie-{self.identity.installation_id.hex}-{self.id.hex}-{self.generation}"

    @property
    def principal(self) -> Principal:
        return Principal(self.consumer_id)

    @property
    def labels(self) -> dict[str, str]:
        return {
            "boxie.installation": str(self.identity.installation_id),
            "boxie.host": self.identity.host_id,
            "boxie.sandbox": str(self.id),
            "boxie.generation": str(self.generation),
            "boxie.consumer": self.consumer_id,
            "boxie.deadline": self.absolute_deadline.isoformat(),
            "boxie.artifact": str(self.spec.artifact_id),
        }


class RegistryFile(WireModel):
    identity: IdentityWire
    records: list[Record] = Field(default_factory=list)


@dataclass
class LiveDeadlines:
    """Process-local elapsed timers; restart always reconciles persisted records."""

    absolute: float
    idle: float | None = None
    disconnect: float | None = None
