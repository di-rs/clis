"""Migrated clock-step assertions against the supervisor execution owner."""

import asyncio
from pathlib import Path

import pytest
from boxie.domain.models import SandboxState
from lifecycle_support import settled, setup


@pytest.mark.parametrize(
    "timer,wall_step",
    [("initial", -7200), ("disconnected", 7200), ("idle", -7200), ("absolute", 7200)],
)
def test_live_deadlines_ignore_wall_steps(tmp_path: Path, wall_step: int, timer: str) -> None:
    async def exercise() -> None:
        supervisor, request, daemon, clock, _ = await setup(tmp_path)
        await settled(supervisor, request.ref, SandboxState.READY)
        epoch = None
        if timer != "initial":
            epoch = await supervisor.attach(request.ref, attachment_order=1)
        if timer == "disconnected":
            assert epoch is not None
            await supervisor.detach(request.ref, epoch)
        clock.advance_wall(wall_step)
        await supervisor.reconcile()
        assert not daemon.deleted_ids
        duration = 3600 if timer == "absolute" else 600 if timer == "idle" else 120
        for _ in range(duration // 30 - 1):
            clock.advance_monotonic(30)
            await supervisor.renew_control_lease(request.ref, supervisor.boot_id)
            if timer == "absolute":
                assert epoch is not None
                await supervisor.write(request.ref, epoch, b"active")
        clock.advance_monotonic(29)
        await supervisor.reconcile()
        assert not daemon.deleted_ids
        assert (
            await supervisor.inspect(request.ref)
        ).absolute_deadline == request.absolute_deadline
        clock.advance_monotonic(1)
        await supervisor.reconcile()
        final = await supervisor.inspect(request.ref)
        assert final.removal_confirmed
        assert final.reason == (
            "expired" if timer == "absolute" else "idle" if timer == "idle" else "disconnected"
        )
        await supervisor.close()

    asyncio.run(exercise())


def test_watchdog_does_not_schedule_normal_cleanup_or_retention(tmp_path: Path) -> None:
    from boxie.domain.models import TerminationReason

    async def exercise() -> None:
        supervisor, request, daemon, _, _ = await setup(tmp_path)
        await settled(supervisor, request.ref, SandboxState.READY)
        daemon.unreachable = True
        await supervisor.remove(request.ref, TerminationReason.REQUESTED)
        daemon.unreachable = False
        await supervisor.start()
        try:
            await asyncio.sleep(0.04)
            assert daemon.resources, "normal cleanup belongs to Temporal retries"
            assert request.ref.id in supervisor.records
            await supervisor.remove(request.ref, TerminationReason.REQUESTED)
            assert not daemon.resources
        finally:
            await supervisor.close()

    asyncio.run(exercise())
