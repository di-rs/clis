"""Nonsecret workflow payloads. Never add admission specs or terminal data here."""

from dataclasses import dataclass

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
