"""Execution wire contracts."""

from dataclasses import asdict
from typing import Annotated
from uuid import UUID

from pydantic import Field

from boxie.contracts.common import ConsumerId, Positive, WireModel
from boxie.contracts.consumer import PolicyWire, ProfileRequest
from boxie.domain.models import (
    ProfileSnapshot,
    SandboxLimits,
    SupervisorIdentity,
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

    def domain(self) -> SupervisorIdentity:
        return SupervisorIdentity(self.installation_id, self.host_id)


class ControlRequest(WireModel):
    consumer_id: ConsumerId
    identity: IdentityWire
    generation: Positive


class ReadyWire(WireModel):
    identity: IdentityWire
    dependencies: dict[str, bool]
