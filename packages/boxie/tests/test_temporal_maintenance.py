"""Embedded work is routed to the owning host and excludes competing owners."""

from pathlib import Path
from uuid import uuid4

import pytest
from boxie.domain.models import OrchestratorIdentity
from boxie.sandbox.execution.ownership import acquire_owner_lock
from boxie.temporal.contracts import lifecycle_queue, maintenance_queue


def test_owner_lock_rejects_a_second_process_and_releases(tmp_path: Path) -> None:
    with acquire_owner_lock(tmp_path):
        with pytest.raises(RuntimeError, match="already owned"):
            with acquire_owner_lock(tmp_path):
                pytest.fail("second owner accepted")
    with acquire_owner_lock(tmp_path):
        pass


def test_task_queues_isolate_installations_hosts_and_maintenance() -> None:
    identity = OrchestratorIdentity(uuid4(), "host-a")
    assert lifecycle_queue(identity) != maintenance_queue(identity)
    assert lifecycle_queue(identity) != lifecycle_queue(OrchestratorIdentity(uuid4(), "host-a"))
    assert lifecycle_queue(identity) != lifecycle_queue(
        OrchestratorIdentity(identity.installation_id, "host-b")
    )


@pytest.mark.parametrize("restriction", ["end_at", "start_at", "limited", "paused"])
def test_schedule_restrictions_cannot_silently_disable_recovery(restriction: str) -> None:
    import asyncio
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace
    from typing import cast

    from boxie.bootstrap.schedules import check_schedules, schedule_definitions
    from temporalio.client import Client, ScheduleActionStartWorkflow
    from temporalio.converter import DataConverter

    async def exercise() -> None:
        identity = OrchestratorIdentity(uuid4(), "host")
        schedules = schedule_definitions(identity)
        converter = DataConverter.default
        for schedule in schedules.values():
            assert isinstance(schedule.action, ScheduleActionStartWorkflow)
            schedule.action.args = await converter.encode(schedule.action.args)
        first = next(iter(schedules.values()))
        if restriction == "end_at":
            first.spec.end_at = datetime.now(UTC) - timedelta(days=1)
        elif restriction == "start_at":
            first.spec.start_at = datetime.now(UTC) + timedelta(days=1)
        elif restriction == "limited":
            first.state.limited_actions = True
            first.state.remaining_actions = 0
        else:
            first.state.paused = True

        class Handle:
            def __init__(self, identifier: str) -> None:
                self.identifier = identifier

            async def describe(self) -> object:
                return SimpleNamespace(schedule=schedules[self.identifier])

        client = cast(Client, SimpleNamespace(data_converter=converter, get_schedule_handle=Handle))
        with pytest.raises(RuntimeError, match="configuration differs"):
            await check_schedules(client, identity)

    asyncio.run(exercise())


def test_existing_owner_lock_survives_read_only_storage_for_restart_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import errno
    from typing import Any

    with acquire_owner_lock(tmp_path):
        pass
    original = Path.open

    def read_only(path: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if path == tmp_path / "owner.lock" and mode == "a":
            raise OSError(errno.EROFS, "read-only filesystem")
        return original(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", read_only)
    with acquire_owner_lock(tmp_path):
        with pytest.raises(RuntimeError, match="already owned"):
            with acquire_owner_lock(tmp_path):
                pytest.fail("second owner accepted")


def test_small_deployment_schedule_intervals() -> None:
    from boxie.bootstrap.schedules import schedule_definitions

    schedules = schedule_definitions(OrchestratorIdentity(uuid4(), "host"))
    assert {
        identifier.rsplit("/", 1)[1]: schedule.spec.intervals[0].every.total_seconds()
        for identifier, schedule in schedules.items()
    } == {"commands": 15, "artifacts": 300, "journal": 60, "retention": 300}


@pytest.mark.parametrize("restriction", [None, "paused", "interval", "queue"])
def test_startup_upgrades_only_exact_legacy_schedules(restriction: str | None) -> None:
    import asyncio
    from datetime import timedelta
    from types import SimpleNamespace
    from typing import Any, cast

    from boxie.bootstrap.schedules import check_schedules, ensure_schedules, schedule_definitions
    from temporalio.client import Client, ScheduleActionStartWorkflow, ScheduleAlreadyRunningError
    from temporalio.converter import DataConverter

    async def exercise() -> None:
        identity = OrchestratorIdentity(uuid4(), "host")
        schedules = schedule_definitions(identity)
        converter = DataConverter.default
        for identifier, schedule in schedules.items():
            schedule.spec.intervals[0].every = timedelta(
                seconds=5 if identifier.endswith("/commands") else 30
            )
            assert isinstance(schedule.action, ScheduleActionStartWorkflow)
            schedule.action.args = await converter.encode(schedule.action.args)
        first = next(iter(schedules.values()))
        if restriction == "paused":
            first.state.paused = True
        elif restriction == "interval":
            first.spec.intervals[0].every = timedelta(seconds=7)
        elif restriction == "queue":
            assert isinstance(first.action, ScheduleActionStartWorkflow)
            first.action.task_queue = "wrong"
        updates = []

        class Handle:
            def __init__(self, identifier: str) -> None:
                self.identifier = identifier

            async def describe(self) -> Any:
                return SimpleNamespace(schedule=schedules[self.identifier])

            async def update(self, updater: Any) -> None:
                update = await updater(SimpleNamespace(description=await self.describe()))
                if update is not None:
                    schedules[self.identifier] = update.schedule
                    updates.append(self.identifier)

        async def create(*args: Any) -> None:
            raise ScheduleAlreadyRunningError()

        client = cast(
            Client,
            SimpleNamespace(
                data_converter=converter, get_schedule_handle=Handle, create_schedule=create
            ),
        )
        if restriction is not None:
            with pytest.raises(RuntimeError, match="configuration differs"):
                await ensure_schedules(client, identity)
            assert updates == []
        else:
            await ensure_schedules(client, identity)
            await check_schedules(client, identity)
            assert len(updates) == 4
            await ensure_schedules(client, identity)
            assert len(updates) == 4

    asyncio.run(exercise())


@pytest.mark.integration
def test_legacy_schedule_upgrade_against_temporal(temporal_stack: object) -> None:
    import asyncio
    from datetime import timedelta
    from typing import Any, cast

    from boxie.bootstrap.schedules import check_schedules, ensure_schedules, schedule_definitions
    from temporal_support import tls
    from temporalio.client import Client
    from temporalio.contrib.pydantic import pydantic_data_converter

    async def exercise() -> None:
        stack = cast(Any, temporal_stack)
        client = await Client.connect(
            stack.address,
            namespace="boxie",
            tls=tls(stack, "boxie-orchestrator"),
            data_converter=pydantic_data_converter,
        )
        identity = OrchestratorIdentity(uuid4(), "schedule-upgrade")
        created = []
        try:
            for identifier, schedule in schedule_definitions(identity).items():
                schedule.spec.intervals[0].every = timedelta(
                    seconds=5 if identifier.endswith("/commands") else 30
                )
                await client.create_schedule(identifier, schedule)
                created.append(identifier)
            await ensure_schedules(client, identity)
            await ensure_schedules(client, identity)
            await check_schedules(client, identity)
            handle = client.get_schedule_handle(created[0])
            await handle.pause()
            with pytest.raises(RuntimeError, match="configuration differs"):
                await ensure_schedules(client, identity)
            assert (await handle.describe()).schedule.state.paused
        finally:
            for identifier in created:
                await client.get_schedule_handle(identifier).delete()

    asyncio.run(exercise())
