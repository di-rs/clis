"""Supervisor execution journal; contains no capacity or artifact-reference authority."""

from datetime import datetime
from pathlib import Path
from uuid import UUID

from pydantic import Field

from boxie.contracts.common import WireModel
from boxie.contracts.consumer import SpecWire, spec_wire
from boxie.contracts.execution import IdentityWire, SnapshotWire
from boxie.domain.lifecycle import EnsureRequest, SandboxRef
from boxie.domain.models import Principal, SandboxState, SupervisorIdentity, TerminationReason
from boxie.persistence.atomic import atomic_write


class ExecutionRecord(WireModel):
    id: UUID
    generation: int
    consumer_id: str
    identity: IdentityWire
    spec: SpecWire
    snapshot: SnapshotWire
    accepted_at: datetime
    absolute_deadline: datetime
    boot_id: UUID
    event_version: int = 1
    state: SandboxState = SandboxState.ACCEPTED
    reason: TerminationReason | None = None
    cleanup_pending: bool = False
    removal_confirmed: bool = False
    creation_uncertain: bool = False
    start_uncertain: bool = False
    operation_id: UUID | None = None
    container_id: str | None = None
    ready_at: datetime | None = None
    last_input_at: datetime | None = None
    disconnected_at: datetime | None = None
    control_lease_until: datetime
    attachment_order: int = Field(default=0, ge=0, le=2**53 - 1)
    attachment_epoch: int = 0
    attached: bool = False
    output_bytes: int = 0

    @property
    def ref(self) -> SandboxRef:
        return SandboxRef(self.id, self.generation)

    @property
    def name(self) -> str:
        return f"boxie-{self.identity.installation_id.hex}-{self.id.hex}-{self.generation}"

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
            "boxie.boot": str(self.boot_id),
        }

    def request(self) -> EnsureRequest:
        return EnsureRequest(
            self.ref,
            Principal(self.consumer_id),
            self.identity.domain(),
            self.spec.domain(),
            self.snapshot.domain(),
            self.accepted_at,
            self.absolute_deadline,
        )

    @classmethod
    def create(cls, request: EnsureRequest, boot: UUID, lease: datetime) -> ExecutionRecord:
        return cls(
            id=request.ref.id,
            generation=request.ref.generation,
            consumer_id=request.principal.consumer_id,
            identity=IdentityWire(
                installation_id=request.identity.installation_id, host_id=request.identity.host_id
            ),
            spec=spec_wire(request.spec),
            snapshot=SnapshotWire.from_domain(request.profile_snapshot),
            accepted_at=request.accepted_at,
            absolute_deadline=request.absolute_deadline,
            boot_id=boot,
            control_lease_until=lease,
        )


class JournalData(WireModel):
    identity: IdentityWire
    records: list[ExecutionRecord] = Field(default_factory=list)


class ExecutionJournal:
    def __init__(self, path: Path, identity: SupervisorIdentity) -> None:
        self.path = path
        self.identity = IdentityWire(
            installation_id=identity.installation_id, host_id=identity.host_id
        )
        self.records: dict[UUID, ExecutionRecord] = {}
        if path.exists():
            data = JournalData.model_validate_json(path.read_bytes())
            if data.identity != self.identity:
                raise ValueError("execution journal installation mismatch")
            self.records = {record.id: record for record in data.records}

    def save(self) -> None:
        atomic_write(
            self.path,
            JournalData(identity=self.identity, records=list(self.records.values()))
            .model_dump_json()
            .encode(),
        )
