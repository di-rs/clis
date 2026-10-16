"""Nonsecret workflow payloads. Never add admission specs or terminal data here."""

from dataclasses import dataclass
from urllib.parse import quote

from boxie.domain.models import OrchestratorIdentity

TASK_QUEUE = "boxie-lifecycle"


@dataclass(frozen=True)
class LifecycleFacts:
    absolute_deadline: float
    desired_stop: bool
    cleaned: bool
    next_deadline: float | None = None


@dataclass(frozen=True)
class LifecycleResult:
    cleaned: bool


@dataclass(frozen=True)
class StopRequest:
    sandbox_id: str
    reason: str


def lifecycle_queue(identity: OrchestratorIdentity) -> str:
    return f"boxie/{identity.installation_id}/{quote(identity.host_id, safe='')}/lifecycle"


def maintenance_queue(identity: OrchestratorIdentity) -> str:
    return f"boxie/{identity.installation_id}/{quote(identity.host_id, safe='')}/maintenance"
