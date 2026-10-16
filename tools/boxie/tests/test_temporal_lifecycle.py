"""Workflow-time faults with deterministic activities; no database clock skipping."""

import asyncio
import importlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

pytestmark = pytest.mark.integration


class Decisions:
    def __init__(self, mode: str) -> None:
        from boxie.temporal.contracts import LifecycleFacts

        self.mode = mode
        self.facts = LifecycleFacts(
            absolute_deadline=(datetime.now(UTC) + timedelta(hours=1)).timestamp(),
            desired_stop=mode == "stop_first",
            cleaned=False,
        )
        self.ensures = self.removals = self.inspections = self.stops = 0
        self.stop_committed = False
        self.removal_attempts: list[int] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        if mode == "cancel_expire":
            self.facts = replace(self.facts, absolute_deadline=0)
        if mode in {"idle", "detached", "absolute"}:
            seconds = {"idle": 600, "detached": 120, "absolute": 3600}[mode]
            self.facts = replace(
                self.facts, next_deadline=self.facts.absolute_deadline - 3600 + seconds
            )

    @activity.defn(name="load_request")
    async def load(self, sandbox_id: str) -> Any:
        if self.mode == "cancel_load":
            self.entered.set()
            await self.release.wait()
        return self.facts

    @activity.defn(name="ensure_sandbox")
    async def ensure(self, sandbox_id: str) -> None:
        self.ensures += 1
        if self.mode == "cancel_ensure":
            self.entered.set()
            await self.release.wait()
        if self.mode == "unsafe_failure":
            try:
                raise RuntimeError("SECRET-NESTED-CONTEXT")
            except RuntimeError as error:
                raise RuntimeError("SECRET-ENV-HTTP-ERROR") from error
        if self.mode in {"idle", "detached", "absolute", "cancel_inspect", "cancel_expire"}:
            return
        if self.mode == "provision_failure":
            raise ApplicationError("safe failure", type="DependencyUnavailable")
        self.facts = replace(self.facts, desired_stop=True)

    @activity.defn(name="inspect_and_renew")
    async def inspect(self, sandbox_id: str) -> Any:
        self.inspections += 1
        if self.mode == "cancel_inspect":
            self.entered.set()
            await self.release.wait()
        return self.facts

    @activity.defn(name="expire_if_current")
    async def expire(self, sandbox_id: str) -> Any:
        if self.mode == "cancel_expire":
            self.entered.set()
            await self.release.wait()
        self.facts = replace(self.facts, desired_stop=True)
        return self.facts

    @activity.defn(name="request_stop")
    async def stop(self, request: dict[str, str]) -> None:
        self.stops += 1
        if self.mode.startswith("terminal_stop_") and self.stops == 1:
            self.cleanup_fault()
        self.stop_committed = True
        self.facts = replace(self.facts, desired_stop=True)

    @activity.defn(name="remove_sandbox")
    async def remove(self, sandbox_id: str) -> None:
        assert self.stop_committed, "removal must follow durable stop"
        self.removals += 1
        self.removal_attempts.append(activity.info().attempt)
        if self.mode.startswith("terminal_remove_") and self.removals == 1:
            self.cleanup_fault()
        if self.mode == "cancel_cleanup":
            self.entered.set()
            await self.release.wait()
        if self.mode == "native_retry" and self.removals <= 100:
            raise ApplicationError("pending", type="CleanupPending")
        if self.mode == "cleanup_failure" and self.removals < 12:
            raise ApplicationError("pending cleanup", type="CleanupPending")
        self.facts = replace(self.facts, cleaned=True)

    def cleanup_fault(self) -> None:
        from boxie.domain.errors import BoxieError

        fault = self.mode.rsplit("_", 1)[1]
        if fault == "application":
            raise ApplicationError(
                "SECRET-CLEANUP-DETAIL", type="InvalidRequest", non_retryable=True
            )
        raise BoxieError(int(fault), "SECRET-CLEANUP-DETAIL")

    @activity.defn(name="record_observation")
    async def record(self, sandbox_id: str) -> None:
        pass

    @activity.defn(name="confirm_removed")
    async def confirm(self, sandbox_id: str) -> bool:
        return self.facts.cleaned

    def activities(self) -> list[Any]:
        return [
            self.load,
            self.ensure,
            self.inspect,
            self.expire,
            self.stop,
            self.remove,
            self.record,
            self.confirm,
        ]


@pytest.mark.parametrize("mode", ["stop_first", "normal", "provision_failure", "cleanup_failure"])
def test_lifecycle_cleanup_and_replay(mode: str, tmp_path: Path) -> None:
    async def exercise() -> None:
        workflow_module = importlib.import_module("boxie.temporal.workflows")
        from temporal_test_server import test_server_path

        async with await WorkflowEnvironment.start_time_skipping(
            test_server_existing_path=str(test_server_path())
        ) as env:
            decisions = Decisions(mode)
            queue = "lifecycle-" + uuid4().hex
            async with Worker(
                env.client,
                task_queue=queue,
                workflows=[workflow_module.SandboxLifecycle],
                activities=decisions.activities(),
            ):
                handle = await env.client.start_workflow(
                    workflow_module.SandboxLifecycle.run,
                    str(uuid4()),
                    id="boxie/sandbox/" + str(uuid4()),
                    task_queue=queue,
                )
                result = await asyncio.wait_for(handle.result(), 90)
                assert result.cleaned
                assert decisions.removals >= 1
                assert decisions.ensures == (
                    0 if mode == "stop_first" else 5 if mode == "provision_failure" else 1
                )
                if mode == "cleanup_failure":
                    assert decisions.removals >= 12
                history = await handle.fetch_history()
                (tmp_path / f"{mode}.json").write_text(history.to_json())
                await Replayer(workflows=[workflow_module.SandboxLifecycle]).replay_workflow(
                    history
                )

    asyncio.run(exercise())


@pytest.mark.parametrize("mode,seconds", [("idle", 600), ("detached", 120), ("absolute", 3600)])
def test_earliest_workflow_deadline(mode: str, seconds: int) -> None:
    async def exercise() -> None:
        from boxie.temporal.workflows import SandboxLifecycle
        from temporal_test_server import test_server_path

        async with await WorkflowEnvironment.start_time_skipping(
            test_server_existing_path=str(test_server_path())
        ) as env:
            decisions = Decisions(mode)
            started = await env.get_current_time()
            async with Worker(
                env.client,
                task_queue="deadlines",
                workflows=[SandboxLifecycle],
                activities=decisions.activities(),
            ):
                handle = await env.client.start_workflow(
                    SandboxLifecycle.run, str(uuid4()), id=str(uuid4()), task_queue="deadlines"
                )
                assert (await handle.result()).cleaned
                history = await handle.fetch_history()
                elapsed = (
                    history.events[-1].event_time.ToDatetime(tzinfo=UTC) - started
                ).total_seconds()
            assert seconds - 2 <= elapsed <= seconds + 2
            assert decisions.inspections >= seconds // 30 - 1

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "mode",
    ["cancel_ensure", "cancel_cleanup", "unsafe_failure", "native_retry"]
    + [
        f"terminal_{operation}_{fault}"
        for operation in ("stop", "remove")
        for fault in ("400", "404", "422", "application")
    ],
)
def test_cancellation_privacy_and_run_boundary(
    mode: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async def exercise() -> None:
        from boxie.temporal.activities import SafeActivityErrors
        from boxie.temporal.workflows import SandboxLifecycle
        from temporal_test_server import test_server_path
        from temporalio.client import WorkflowHistory

        async with await WorkflowEnvironment.start_time_skipping(
            test_server_existing_path=str(test_server_path())
        ) as env:
            decisions = Decisions(mode)
            async with Worker(
                env.client,
                task_queue="edges",
                workflows=[SandboxLifecycle],
                activities=decisions.activities(),
                interceptors=[SafeActivityErrors()],
            ):
                handle = await env.client.start_workflow(
                    SandboxLifecycle.run, str(uuid4()), id=str(uuid4()), task_queue="edges"
                )
                if mode.startswith("cancel_"):
                    await asyncio.wait_for(decisions.entered.wait(), 5)
                    if mode == "cancel_ensure":
                        # Test server1.40.0 rejects cancellation racing the eager
                        # inspection completion (ACTIVITY_UNKNOWN). Production1.32
                        # exercises that exact race separately; here cancel the
                        # still-blocked provision after inspection has settled.
                        for _ in range(300):
                            history = await handle.fetch_history()
                            scheduled = {
                                e.event_id
                                for e in history.events
                                if e.activity_task_scheduled_event_attributes.activity_type.name
                                == "inspect_and_renew"
                            }
                            if any(
                                e.activity_task_completed_event_attributes.scheduled_event_id
                                in scheduled
                                for e in history.events
                            ):
                                break
                            await asyncio.sleep(0.01)
                        else:
                            raise AssertionError("inspection did not settle")
                    await handle.cancel()
                    if mode == "cancel_cleanup":
                        await asyncio.sleep(0.1)
                        decisions.release.set()
                result = await asyncio.wait_for(handle.result(), 90)
                assert result.cleaned
                runs = []
                run_id = handle.first_execution_run_id
                while run_id:
                    history = await env.client.get_workflow_handle(
                        handle.id, run_id=run_id
                    ).fetch_history()
                    serialized = history.to_json()
                    (tmp_path / f"{mode}-{run_id}.json").write_text(serialized)
                    for event in history.events:
                        assert b"SECRET-" not in event.SerializeToString()
                    await Replayer(workflows=[SandboxLifecycle]).replay_workflow(
                        WorkflowHistory.from_json(handle.id, serialized)
                    )
                    runs.append(run_id)
                    last = history.events[-1]
                    run_id = (
                        last.workflow_execution_continued_as_new_event_attributes.new_execution_run_id
                        or None
                    )
                assert len(runs) == 1
                if mode == "native_retry":
                    # Server-side retries retain one scheduled command even after
                    # a long outage; workflow loops must not grow cleanup history.
                    assert decisions.removal_attempts == list(range(1, 102))
                    cleanup_commands = [
                        event.activity_task_scheduled_event_attributes.activity_type.name
                        for event in history.events
                        if event.HasField("activity_task_scheduled_event_attributes")
                    ]
                    assert cleanup_commands.count("request_stop") == 1
                    assert cleanup_commands.count("remove_sandbox") == 1
                    assert "confirm_removed" not in cleanup_commands
                if mode == "unsafe_failure":
                    assert decisions.ensures == 5
                if mode.startswith("terminal_stop_"):
                    assert decisions.stops == 2
                if mode.startswith("terminal_remove_"):
                    assert decisions.removal_attempts == [1, 2]
                assert "SECRET-" not in caplog.text

    asyncio.run(exercise())
