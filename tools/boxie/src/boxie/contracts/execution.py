"""Execution wire contracts."""

from dataclasses import asdict
from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, Field

from boxie.contracts.common import ConsumerId, Positive, WireModel
from boxie.contracts.consumer import PolicyWire, ProfileRequest, SpecWire, spec_wire
from boxie.domain.lifecycle import EnsureRequest as DomainEnsureRequest
from boxie.domain.lifecycle import OrchestratorObservation, SandboxRef
from boxie.domain.models import (
    OrchestratorIdentity,
    Principal,
    ProfileSnapshot,
    SandboxLimits,
    SandboxState,
    TerminationReason,
)


class LimitsWire(WireModel):
    memory_bytes: Positive = 536870912
    cpu_nanos: Positive = 1000000000
    pids: Positive = 256
    workspace_bytes: Positive = 67108864
    tmp_bytes: Positive = 16777216
    nofile: Positive = 1024
    output_bytes: Positive = 16777216

    def domain(self) -> SandboxLimits:
        return SandboxLimits(**self.model_dump())


class AdmissionProfileRequest(ProfileRequest):
    policy: PolicyWire = Field(default_factory=PolicyWire)


class SnapshotWire(ProfileRequest):
    config_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    image_digest: str
    artifact_target: str
    limits: LimitsWire

    def domain(self) -> ProfileSnapshot:
        return ProfileSnapshot(
            self.profile_id,
            self.config_digest,
            self.image_digest,
            self.command,
            self.env,
            self.artifact_target,
            self.limits.domain(),
        )

    @classmethod
    def from_domain(cls, snapshot: ProfileSnapshot) -> SnapshotWire:
        return cls(
            profile_id=snapshot.profile_id,
            config_digest=snapshot.config_digest,
            image_digest=snapshot.image_digest,
            command=snapshot.command,
            env=dict(snapshot.env),
            artifact_target=snapshot.artifact_target,
            limits=LimitsWire(**asdict(snapshot.limits)),
        )


class IdentityWire(WireModel):
    installation_id: UUID
    host_id: Annotated[str, Field(min_length=1, max_length=128)]

    def domain(self) -> OrchestratorIdentity:
        return OrchestratorIdentity(self.installation_id, self.host_id)


class ControlRequest(WireModel):
    consumer_id: ConsumerId
    identity: IdentityWire
    generation: Positive


class ReadyWire(WireModel):
    identity: IdentityWire
    dependencies: dict[str, bool]


class RefWire(WireModel):
    id: UUID
    generation: Positive

    def domain(self) -> SandboxRef:
        return SandboxRef(self.id, self.generation)


class DurableEnsureWire(WireModel):
    """Only trusted control callers may submit an admitted execution request."""

    ref: RefWire
    consumer_id: ConsumerId
    identity: IdentityWire
    spec: SpecWire
    profile_snapshot: SnapshotWire
    accepted_at: AwareDatetime
    absolute_deadline: AwareDatetime

    @classmethod
    def from_domain(cls, request: DomainEnsureRequest) -> DurableEnsureWire:
        return cls(
            ref=RefWire(id=request.ref.id, generation=request.ref.generation),
            consumer_id=request.principal.consumer_id,
            identity=IdentityWire(
                installation_id=request.identity.installation_id, host_id=request.identity.host_id
            ),
            spec=spec_wire(request.spec),
            profile_snapshot=SnapshotWire.from_domain(request.profile_snapshot),
            accepted_at=request.accepted_at,
            absolute_deadline=request.absolute_deadline,
        )

    def domain(self) -> DomainEnsureRequest:
        return DomainEnsureRequest(
            self.ref.domain(),
            Principal(self.consumer_id),
            self.identity.domain(),
            self.spec.domain(),
            self.profile_snapshot.domain(),
            self.accepted_at,
            self.absolute_deadline,
        )


class ObservationWire(WireModel):
    sandbox_id: UUID
    consumer_id: ConsumerId
    identity: IdentityWire
    generation: Positive
    boot_id: UUID
    event_version: Positive
    observed_at: AwareDatetime
    state: SandboxState
    resource_present: bool
    removal_confirmed: bool
    cleanup_pending: bool
    reason: TerminationReason | None
    ready_at: AwareDatetime | None
    last_input_at: AwareDatetime | None
    disconnected_at: AwareDatetime | None
    attachment_epoch: Annotated[int, Field(strict=True, ge=0)]
    absolute_deadline: AwareDatetime | None
    control_lease_until: AwareDatetime | None
    container_id: str | None

    @classmethod
    def from_domain(cls, observation: OrchestratorObservation) -> ObservationWire:
        body = asdict(observation)
        body["consumer_id"] = body.pop("principal")["consumer_id"]
        return cls.model_validate(body)

    def domain(self) -> OrchestratorObservation:
        body = self.model_dump()
        body["principal"] = Principal(body.pop("consumer_id"))
        body["identity"] = self.identity.domain()
        return OrchestratorObservation(**body)


class BootWire(WireModel):
    identity: IdentityWire
    boot_id: UUID


class ExecutionControlWire(ControlRequest):
    boot_id: UUID
    reason: TerminationReason = TerminationReason.REQUESTED


class ExecutionExpiryWire(ExecutionControlWire):
    observed_event_version: Positive
