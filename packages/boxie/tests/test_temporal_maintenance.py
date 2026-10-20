"""Embedded work is routed to the owning host and excludes competing owners."""

from pathlib import Path
from uuid import uuid4

import pytest
from boxie.domain.models import SupervisorIdentity
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
    identity = SupervisorIdentity(uuid4(), "host-a")
    assert lifecycle_queue(identity) != maintenance_queue(identity)
    assert lifecycle_queue(identity) != lifecycle_queue(SupervisorIdentity(uuid4(), "host-a"))
    assert lifecycle_queue(identity) != lifecycle_queue(
        SupervisorIdentity(identity.installation_id, "host-b")
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
        identity = SupervisorIdentity(uuid4(), "host")
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
