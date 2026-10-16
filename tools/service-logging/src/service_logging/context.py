"""Validated context captured by producers, never looked up by the writer."""

import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar

_context: ContextVar[dict[str, str | int | float] | None] = ContextVar("log_context", default=None)
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_IDS = ("request_id", "sandbox_id", "run_id", "operation_id")


def validated(fields: Mapping[str, object]) -> dict[str, str | int | float]:
    result: dict[str, str | int | float] = {}
    # Fixed iteration, no arbitrary-object repr/conversion or nested traversal.
    for key in _IDS:
        value = fields.get(key)
        if type(value) is str and len(value) == 36 and _UUID.fullmatch(value):
            result[key] = value
    workflow = fields.get("workflow_id")
    if (
        type(workflow) is str
        and len(workflow) == 50
        and workflow.startswith("boxie/sandbox/")
        and _UUID.fullmatch(workflow[14:])
    ):
        result["workflow_id"] = workflow
    for key in ("activity_attempt", "generation"):
        value = fields.get(key)
        if type(value) is int and 0 <= value <= 2**31 - 1:
            result[key] = value
    duration = fields.get("duration_ms")
    if (type(duration) is int or type(duration) is float) and 0 <= duration <= 86400000:
        result["duration_ms"] = duration
    for key, allowed in (
        ("outcome", ("succeeded", "failed", "pending", "cancelled")),
        ("reason", ("unavailable", "invalid", "timeout", "cancelled", "unknown")),
    ):
        value = fields.get(key)
        if type(value) is str and value in allowed:
            result[key] = value
    return result


@contextmanager
def bind_context(**fields: object) -> Iterator[None]:
    token = _context.set({**(_context.get() or {}), **validated(fields)})
    try:
        yield
    finally:
        _context.reset(token)


def current_context() -> dict[str, str | int | float]:
    return (_context.get() or {}).copy()
