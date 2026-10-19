"""Nonsecret workflow payloads. Never add admission specs or terminal data here."""

from urllib.parse import quote

from pydantic import BaseModel, ConfigDict

from boxie.domain.models import SupervisorIdentity

TASK_QUEUE = "boxie-lifecycle"


class WorkflowPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)


class LifecycleFacts(WorkflowPayload):
    absolute_deadline: float
    desired_stop: bool
    cleaned: bool
    next_deadline: float | None = None


class LifecycleResult(WorkflowPayload):
    cleaned: bool


class StopRequest(WorkflowPayload):
    sandbox_id: str
    reason: str


def lifecycle_queue(identity: SupervisorIdentity) -> str:
    return f"boxie/{identity.installation_id}/{quote(identity.host_id, safe='')}/lifecycle"


def maintenance_queue(identity: SupervisorIdentity) -> str:
    return f"boxie/{identity.installation_id}/{quote(identity.host_id, safe='')}/maintenance"
