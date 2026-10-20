"""Bounded lifecycle diagnostics, invoked only after acknowledged durable writes."""

from collections.abc import Callable
from contextlib import nullcontext
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from boxie.observability.telemetry.context import request_context
from boxie.observability.telemetry.logging import configured_dispatcher

EVENTS = {
    "sandbox.accepted": "Sandbox accepted",
    "provisioning.completed": "Provisioning completed",
    "activity.failed": "Activity attempt failed",
    "sandbox.ready": "Sandbox readiness observed",
    "terminal.attached": "Terminal attached",
    "terminal.detached": "Terminal detached",
    "termination.requested": "Termination requested",
    "cleanup.completed": "Cleanup confirmed",
    "cleanup.failed": "Cleanup attempt failed",
    "reconciliation.completed": "Reconciliation attempt completed",
}


class Transition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    event: str = Field(min_length=1)
    sandbox_id: UUID | None = None
    request_id: UUID | None = None
    outcome: str | None = None
    reason: str | None = None
    duration_seconds: float | None = None


type DiagnosticSink = Callable[[Transition], None]


def emit(event: Transition) -> None:
    dispatcher = configured_dispatcher()
    if dispatcher is not None:
        dispatcher.emit(
            event.event,
            sandbox_id=str(event.sandbox_id) if event.sandbox_id else None,
            outcome=event.outcome,
            reason=event.reason,
            duration_ms=event.duration_seconds * 1000
            if event.duration_seconds is not None
            else None,
        )


def deliver(sink: DiagnosticSink, *events: Transition) -> None:
    for event in events:
        try:
            # Durable results explicitly carry even an unknown origin; transient
            # events retain their connection/activity context.
            with (
                request_context(str(event.request_id) if event.request_id else None)
                if "request_id" in event.model_fields_set
                else nullcontext()
            ):
                sink(event)
        except Exception:
            pass  # Diagnostics never change a committed lifecycle result.
