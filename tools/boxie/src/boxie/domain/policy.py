"""Admission policy applies equally to direct and remote callers."""

from dataclasses import fields

from .errors import BoxieError
from .models import LifetimePolicy, SandboxLimits


def validate_policy(policy: LifetimePolicy, maximum: LifetimePolicy | None = None) -> None:
    maximum = maximum or LifetimePolicy()
    for item in fields(policy):
        value = getattr(policy, item.name)
        if type(value) is not int or not 0 < value <= getattr(maximum, item.name):
            raise BoxieError(422, f"invalid lifetime {item.name}")


def validate_limits(limits: SandboxLimits) -> None:
    maximum = SandboxLimits()
    for item in fields(limits):
        value = getattr(limits, item.name)
        if type(value) is not int or not 0 < value <= getattr(maximum, item.name):
            raise BoxieError(422, f"invalid sandbox limit {item.name}")
