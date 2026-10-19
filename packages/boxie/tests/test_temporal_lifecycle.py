"""Workflow-time faults with deterministic activities; no database clock skipping."""

import asyncio
import importlib
from datetime import UTC
from pathlib import Path
from uuid import uuid4

import pytest
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker
from workflow_support import Decisions as Decisions

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("mode", ["stop_first", "normal", "provision_failure"])
def test_lifecycle_cleanup_and_replay(mode: str, tmp_path: Path) -> None:
    async def exercise() -> None:
        workflow_module = importlib.import_module("boxie.temporal.workflows")
        from temporal_test_server import test_server_path

        async with await WorkflowEnvironment.start_time_skipping(
            test_server_existing_path=str(test_server_path()),
            data_converter=pydantic_data_converter,
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
                history = await handle.fetch_history()
                (tmp_path / f"{mode}.json").write_text(history.to_json())
                await Replayer(
                    workflows=[workflow_module.SandboxLifecycle],
                    data_converter=pydantic_data_converter,
                ).replay_workflow(history)

    asyncio.run(exercise())


# The workflow receives a timestamp, not a policy cause. Cover an earlier
# policy deadline and the absolute bound; execution tests own each timer policy.
@pytest.mark.parametrize("mode,seconds", [("detached", 120), ("absolute", 3600)])
def test_earliest_workflow_deadline(mode: str, seconds: int) -> None:
    async def exercise() -> None:
        from boxie.temporal.workflows import SandboxLifecycle
        from temporal_test_server import test_server_path

        async with await WorkflowEnvironment.start_time_skipping(
            test_server_existing_path=str(test_server_path()),
            data_converter=pydantic_data_converter,
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
    [
        "cancel_ensure",
        "cancel_cleanup",
        "unsafe_failure",
        "native_retry",
        "terminal_stop_400",
        "terminal_remove_404",
        "terminal_stop_422",
        "terminal_stop_application",
        "terminal_remove_application",
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
            test_server_existing_path=str(test_server_path()),
            data_converter=pydantic_data_converter,
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
                    await Replayer(
                        workflows=[SandboxLifecycle], data_converter=pydantic_data_converter
                    ).replay_workflow(WorkflowHistory.from_json(handle.id, serialized))
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
