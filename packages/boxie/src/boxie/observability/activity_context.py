"""Best-effort diagnostic lookup using capacity separate from lifecycle writes."""

import asyncio
from collections.abc import Awaitable, Callable
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlmodel import select
from temporalio import activity
from temporalio.worker import ExecuteActivityInput

from boxie.observability.telemetry.context import parse_request_id, request_context
from boxie.persistence.database import SessionFactory
from boxie.persistence.models import Sandbox
from boxie.temporal.contracts import StopRequest

type ActivityContextResolver = Callable[[ExecuteActivityInput], Awaitable[str | None]]


def activity_sandbox_id(input: ExecuteActivityInput) -> str | None:
    name = activity.info().activity_type
    if len(input.args) != 1:
        return None
    argument = input.args[0]
    if name == "request_stop" and isinstance(argument, StopRequest):
        return parse_request_id(argument.sandbox_id)
    if name in {"load_request", "ensure_sandbox", "inspect_and_renew", "remove_sandbox"}:
        return parse_request_id(argument)
    return None


def build_activity_context_resolver(sessions: SessionFactory) -> ActivityContextResolver:
    async def resolve(input: ExecuteActivityInput) -> str | None:
        info = activity.info()
        background = str(
            uuid5(NAMESPACE_URL, f"boxie/activity/{info.workflow_run_id}/{info.activity_id}")
        )
        name = info.activity_type
        if name not in {"ensure_sandbox", "remove_sandbox", "request_stop"}:
            return background
        identifier = activity_sandbox_id(input)
        if identifier is None:
            return None
        try:
            with request_context(None):
                async with asyncio.timeout(0.1), sessions() as session:
                    origins = (
                        await session.exec(
                            select(Sandbox.creation_request_id, Sandbox.stop_request_id).where(
                                Sandbox.id == UUID(identifier)
                            )
                        )
                    ).one_or_none()
            if origins is None:
                return None
            creation, stop = origins
            if name == "ensure_sandbox":
                return str(creation) if creation else None
            if name == "remove_sandbox" or stop is not None:
                return str(stop) if stop else None
            request = input.args[0]
            if isinstance(request, StopRequest) and request.reason == "provisioning_failed":
                return str(creation) if creation else None
            return background
        except Exception:
            return None

    return resolve
