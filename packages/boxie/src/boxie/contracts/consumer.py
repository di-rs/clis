"""Consumer wire contracts."""

from dataclasses import asdict
from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import Field

from boxie.contracts.common import WireModel
from boxie.domain.models import (
    ArtifactView,
    LifetimePolicy,
    SandboxSpec,
    SandboxState,
    SandboxView,
    TerminationReason,
)


class PolicyWire(WireModel):
    max_seconds: Annotated[int, Field(strict=True, gt=0, le=3600)] = 3600
    idle_seconds: Annotated[int, Field(strict=True, gt=0, le=600)] = 600
    disconnect_seconds: Annotated[int, Field(strict=True, gt=0, le=120)] = 120

    def domain(self) -> LifetimePolicy:
        return LifetimePolicy(**self.model_dump())


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


class CreateSandboxWire(WireModel):
    artifact_id: UUID

    def domain(self) -> SandboxSpec:
        return SandboxSpec(
            self.artifact_id,
            "default",
            ("/opt/boxie/shell.sh",),
            {"TERM": "xterm-256color"},
        )


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


def spec_wire(spec: SandboxSpec) -> SpecWire:
    return SpecWire(
        artifact_id=spec.artifact_id,
        profile_id=spec.profile_id,
        command=spec.command,
        env=dict(spec.env),
        policy=PolicyWire(**asdict(spec.policy)),
    )
