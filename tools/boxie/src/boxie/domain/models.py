"""Transport-independent contracts shared by the Boxie application ports."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID


@dataclass(frozen=True)
class Principal:
    consumer_id: str


@dataclass(frozen=True)
class LifetimePolicy:
    max_seconds: int = 3600
    idle_seconds: int = 600
    disconnect_seconds: int = 120


@dataclass(frozen=True)
class SandboxLimits:
    memory_bytes: int = 536870912
    cpu_nanos: int = 1000000000
    pids: int = 256
    workspace_bytes: int = 67108864
    tmp_bytes: int = 16777216
    nofile: int = 1024
    output_bytes: int = 16777216


@dataclass(frozen=True)
class SandboxSpec:
    artifact_id: UUID
    profile_id: str
    command: tuple[str, ...]
    env: Mapping[str, str]
    policy: LifetimePolicy = field(default_factory=LifetimePolicy)

    def __post_init__(self) -> None:
        object.__setattr__(self, "env", MappingProxyType(dict(self.env)))


@dataclass(frozen=True)
class ProfileSnapshot:
    profile_id: str
    config_digest: str
    image_digest: str
    command: tuple[str, ...]
    env: Mapping[str, str]
    artifact_target: str
    limits: SandboxLimits

    def __post_init__(self) -> None:
        object.__setattr__(self, "env", MappingProxyType(dict(self.env)))


class SandboxState(StrEnum):
    ACCEPTED = "accepted"
    PROVISIONING = "provisioning"
    READY = "ready"
    TERMINATING = "terminating"
    TERMINATED = "terminated"
    FAILED = "failed"


class TerminationReason(StrEnum):
    REQUESTED = "requested"
    EXPIRED = "expired"
    IDLE = "idle"
    DISCONNECTED = "disconnected"
    OUTPUT_LIMIT = "output_limit"
    EXITED = "exited"
    PROVISIONING_FAILED = "provisioning_failed"
    AGENT_RESTART = "agent_restart"
    AGENT_SHUTDOWN = "agent_shutdown"


@dataclass(frozen=True)
class SandboxView:
    id: UUID
    state: SandboxState
    created_at: datetime
    absolute_deadline: datetime
    reason: TerminationReason | None
    cleanup_pending: bool


@dataclass(frozen=True)
class ArtifactView:
    id: UUID
    sha256: str
    retired: bool


@dataclass(frozen=True)
class ArtifactPutResult:
    artifact: ArtifactView
    created: bool


@dataclass(frozen=True)
class AgentIdentity:
    installation_id: UUID
    host_id: str
