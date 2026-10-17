"""Admission dependencies; deployment composition injects the concrete adapters."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

DependencyCheck = Callable[[], Awaitable[bool]]


@dataclass(frozen=True)
class AdmissionReadiness:
    oldest_command_seconds: int = 30
    max_backlog: int = 100
    dependency_timeout_seconds: float = 5

    def __post_init__(self) -> None:
        if min(self.oldest_command_seconds, self.max_backlog, self.dependency_timeout_seconds) <= 0:
            raise ValueError("readiness bounds must be positive")
