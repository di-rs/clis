"""Strict private wire schemas. Domain contracts remain ordinary dataclasses."""

from dataclasses import asdict
from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from boxie.domain.models import (
    AgentIdentity,
    LifetimePolicy,
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
