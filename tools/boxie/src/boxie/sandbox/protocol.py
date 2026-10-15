"""Strict private wire schemas. Domain contracts remain ordinary dataclasses."""

from dataclasses import asdict
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from boxie.domain.lifecycle import AgentObservation, ExpiryRequest, SandboxRef
from boxie.domain.lifecycle import EnsureRequest as DomainEnsureRequest
from boxie.domain.models import (
    AgentIdentity,
    ArtifactView,
    LifetimePolicy,
    Principal,
    ProfileSnapshot,
    SandboxLimits,
    SandboxSpec,
    SandboxState,
    SandboxView,
    TerminationReason,
)

ConsumerId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")]
Positive = Annotated[int, Field(strict=True, gt=0)]


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PolicyWire(WireModel):
    max_seconds: Annotated[int, Field(strict=True, gt=0, le=3600)] = 3600
    idle_seconds: Annotated[int, Field(strict=True, gt=0, le=600)] = 600
    disconnect_seconds: Annotated[int, Field(strict=True, gt=0, le=120)] = 120

    def domain(self) -> LifetimePolicy:
        return LifetimePolicy(**self.model_dump())


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


class ProfileRequest(WireModel):
    profile_id: Annotated[str, Field(min_length=1, max_length=128)]
    command: Annotated[tuple[str, ...], Field(min_length=1, max_length=64)]
    env: dict[str, str] = Field(default_factory=dict)


class AdmissionProfileRequest(ProfileRequest):
    policy: PolicyWire = Field(default_factory=PolicyWire)


class SpecWire(ProfileRequest):
    artifact_id: UUID
    policy: PolicyWire = Field(default_factory=PolicyWire)

    def domain(self) -> SandboxSpec:
        return SandboxSpec(
            self.artifact_id, self.profile_id, self.command, self.env, self.policy.domain()
        )


class SubmitRequest(WireModel):
    consumer_id: ConsumerId
    idempotency_key: Annotated[str, Field(min_length=1, max_length=200)]
    spec: SpecWire


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

    def domain(self) -> AgentIdentity:
        return AgentIdentity(self.installation_id, self.host_id)


class ControlRequest(WireModel):
    consumer_id: ConsumerId
    identity: IdentityWire
    generation: Positive


class EnsureRequest(ControlRequest):
    snapshot: SnapshotWire
    artifact_id: UUID
    deadline: datetime

    @field_validator("deadline")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("deadline requires timezone")
        return value


class RemoveRequest(ControlRequest):
    reason: TerminationReason = TerminationReason.REQUESTED


class ViewWire(WireModel):
    id: UUID
    state: SandboxState
    created_at: datetime
    absolute_deadline: datetime
    reason: TerminationReason | None
    cleanup_pending: bool

    @classmethod
    def from_domain(cls, view: SandboxView) -> ViewWire:
        return cls(**asdict(view))

    def domain(self) -> SandboxView:
        return SandboxView(**self.model_dump())


class ArtifactWire(WireModel):
    id: UUID
    sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    retired: bool

    def domain(self) -> ArtifactView:
        return ArtifactView(**self.model_dump())


class ReadyWire(WireModel):
    identity: IdentityWire
    dependencies: dict[str, bool]


class StatusWire(WireModel):
    type: Literal["status"]
    reason: TerminationReason


class ResizeWire(WireModel):
    type: str = Field(pattern="^resize$")
    columns: Annotated[int, Field(strict=True, ge=1, le=500)]
    rows: Annotated[int, Field(strict=True, ge=1, le=300)]


def spec_wire(spec: SandboxSpec) -> SpecWire:
    return SpecWire(
        artifact_id=spec.artifact_id,
        profile_id=spec.profile_id,
        command=spec.command,
        env=dict(spec.env),
        policy=PolicyWire(**asdict(spec.policy)),
    )


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
    def from_domain(cls, observation: AgentObservation) -> ObservationWire:
        body = asdict(observation)
        body["consumer_id"] = body.pop("principal")["consumer_id"]
        return cls.model_validate(body)

    def domain(self) -> AgentObservation:
        body = self.model_dump()
        body["principal"] = Principal(body.pop("consumer_id"))
        body["identity"] = self.identity.domain()
        return AgentObservation(**body)


class ExpiryWire(WireModel):
    ref: RefWire
    reason: TerminationReason
    observed_event_version: Positive
    boot_id: UUID

    def domain(self) -> ExpiryRequest:
        return ExpiryRequest(
            self.ref.domain(), self.reason, self.observed_event_version, self.boot_id
        )
