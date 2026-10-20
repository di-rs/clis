"""Activity correlation remains bounded and independent of lifecycle availability."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from boxie.observability.telemetry.context import current_request_id, request_context
from boxie.persistence.database import SessionFactory
from temporalio import activity
from temporalio.worker import ExecuteActivityInput


def test_background_activity_has_retry_stable_context(monkeypatch: pytest.MonkeyPatch) -> None:
    from boxie.observability.activity_context import build_activity_context_resolver

    async def exercise() -> None:
        info = SimpleNamespace(
            activity_type="maintain", workflow_run_id="run", activity_id="one", attempt=1
        )
        monkeypatch.setattr(activity, "info", lambda: info)
        resolver = build_activity_context_resolver(cast(SessionFactory, None))
        argument = cast(ExecuteActivityInput, SimpleNamespace(args=["not a sandbox"]))
        first = await resolver(argument)
        info.attempt = 2
        assert await resolver(argument) == first
        assert first is not None and UUID(first).version == 5
        info.activity_id = "two"
        assert await resolver(argument) != first

    asyncio.run(exercise())


def test_context_lookup_failure_does_not_fail_activity(monkeypatch: pytest.MonkeyPatch) -> None:
    from boxie.observability.activity_context import build_activity_context_resolver

    async def exercise() -> None:
        info = SimpleNamespace(
            activity_type="ensure_sandbox", workflow_run_id="run", activity_id="one"
        )
        monkeypatch.setattr(activity, "info", lambda: info)

        class Session:
            async def __aenter__(self) -> Any:
                assert current_request_id() is None
                await asyncio.sleep(10)

            async def __aexit__(self, *args: Any) -> None:
                pass

        resolver = build_activity_context_resolver(cast(SessionFactory, Session))
        with request_context("11111111-1111-1111-1111-111111111111"):
            async with asyncio.timeout(0.5):
                assert (
                    await resolver(
                        cast(
                            ExecuteActivityInput,
                            SimpleNamespace(args=["22222222-2222-2222-2222-222222222222"]),
                        )
                    )
                    is None
                )
            assert current_request_id() == "11111111-1111-1111-1111-111111111111"

    asyncio.run(exercise())


def test_activity_error_logging_keeps_resolved_resource_and_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    from boxie.observability.telemetry.context import current_context
    from boxie.temporal import activities
    from temporalio.exceptions import ApplicationError
    from temporalio.testing import ActivityEnvironment
    from temporalio.worker import ActivityInboundInterceptor

    async def exercise() -> None:
        environment = ActivityEnvironment()
        environment.info = replace(environment.info, activity_type="ensure_sandbox")
        seen: list[dict[str, str | int | float]] = []
        monkeypatch.setattr(activities, "emit", lambda event: seen.append(current_context()))

        async def resolve(input: ExecuteActivityInput) -> str:
            assert current_request_id() is None
            return "11111111-1111-1111-1111-111111111111"

        class Body(ActivityInboundInterceptor):
            async def execute_activity(self, input: ExecuteActivityInput) -> Any:
                assert current_request_id() == "11111111-1111-1111-1111-111111111111"
                raise OSError("PRIVATE_SENTINEL")

        inbound = activities.SafeActivityInbound(
            Body(cast(ActivityInboundInterceptor, None)), context_resolver=resolve
        )
        with request_context("33333333-3333-3333-3333-333333333333"):
            with pytest.raises(ApplicationError, match="lifecycle operation pending"):
                await environment.run(
                    inbound.execute_activity,
                    cast(
                        ExecuteActivityInput,
                        SimpleNamespace(args=["22222222-2222-2222-2222-222222222222"]),
                    ),
                )
            assert current_request_id() == "33333333-3333-3333-3333-333333333333"
        assert seen[0]["request_id"] == "11111111-1111-1111-1111-111111111111"
        assert seen[0]["sandbox_id"] == "22222222-2222-2222-2222-222222222222"
        assert seen[0]["activity_attempt"] == 1

    asyncio.run(exercise())


@pytest.mark.integration
def test_activity_retry_and_restart_keep_action_origin(
    postgres_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uuid import uuid4

    from boxie.domain.models import TerminationReason
    from boxie.observability.activity_context import build_activity_context_resolver
    from boxie.temporal.contracts import StopRequest
    from database_support import OWNER, database, upload

    async def exercise() -> None:
        creation, stop = uuid4(), uuid4()
        async with database(postgres_dsn, tmp_path) as (life, artifacts, sessions, _):
            with request_context(str(creation)):
                view = await life.submit(OWNER, "activity-origin", await upload(artifacts))
            with request_context(str(stop)):
                await life.terminate(OWNER, view.id, TerminationReason.REQUESTED)
            info = SimpleNamespace(
                activity_type="ensure_sandbox", workflow_run_id="run", activity_id="one", attempt=1
            )
            monkeypatch.setattr(activity, "info", lambda: info)
            argument = cast(ExecuteActivityInput, SimpleNamespace(args=[str(view.id)]))
            resolver = build_activity_context_resolver(sessions)
            assert await resolver(argument) == str(creation)
            info.attempt = 2
            assert await build_activity_context_resolver(sessions)(argument) == str(creation)
            info.activity_type = "remove_sandbox"
            assert await resolver(argument) == str(stop)
            info.activity_type = "request_stop"
            argument.args = [StopRequest(sandbox_id=str(view.id), reason="provisioning_failed")]
            assert await resolver(argument) == str(stop)

    asyncio.run(exercise())
