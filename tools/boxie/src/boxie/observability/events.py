"""Bounded domain diagnostics, invoked only after acknowledged durable writes."""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from service_telemetry.logging import configured_runtime

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


@dataclass(frozen=True)
class Transition:
    event: str
    sandbox_id: UUID | None = None
    outcome: str | None = None
    reason: str | None = None
    duration_seconds: float | None = None


type DiagnosticSink = Callable[[Transition], None]


def emit(event: Transition) -> None:
    runtime = configured_runtime()
    if runtime is not None:
        runtime.emit(
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
            sink(event)
        except Exception:
            pass  # Diagnostics never change a committed lifecycle result.
