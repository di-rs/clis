"""Admission dependencies; deployment composition injects the concrete adapters."""

from collections.abc import Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field

DependencyCheck = Callable[[], Awaitable[bool]]


class AdmissionReadiness(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    oldest_command_seconds: int = Field(default=30, gt=0)
    max_backlog: int = Field(default=100, gt=0)
    dependency_timeout_seconds: float = Field(default=5, gt=0, allow_inf_nan=False)
