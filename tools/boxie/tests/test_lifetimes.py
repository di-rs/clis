"""Migrated clock-step assertions against the durable execution owner."""

import asyncio
from pathlib import Path

import pytest
from boxie.domain.models import SandboxState
from test_orchestrator_lifecycle import settled, setup


@pytest.mark.parametrize(
    "timer,wall_step",
    [("initial", -7200), ("disconnected", 7200), ("idle", -7200), ("absolute", 7200)],
)
def test_live_deadlines_ignore_wall_steps(tmp_path: Path, wall_step: int, timer: str) -> None:
    async def exercise() -> None:
        orchestrator, request, daemon, clock, _ = await setup(tmp_path)
        await settled(orchestrator, request.ref, SandboxState.READY)
        epoch = None
        if timer != "initial":
            epoch = await orchestrator.attach(request.ref, attachment_order=1)
        if timer == "disconnected":
            assert epoch is not None
            await orchestrator.detach(request.ref, epoch)
        clock.advance_wall(wall_step)
        await orchestrator.reconcile()
        assert not daemon.deleted_ids
        duration = 3600 if timer == "absolute" else 600 if timer == "idle" else 120
        for _ in range(duration // 30 - 1):
            clock.advance_monotonic(30)
            await orchestrator.renew_control_lease(request.ref, orchestrator.boot_id)
            if timer == "absolute":
                assert epoch is not None
                await orchestrator.write(request.ref, epoch, b"active")
        clock.advance_monotonic(29)
        await orchestrator.reconcile()
        assert not daemon.deleted_ids
        assert (
            await orchestrator.inspect(request.ref)
        ).absolute_deadline == request.absolute_deadline
        clock.advance_monotonic(1)
        await orchestrator.reconcile()
        final = await orchestrator.inspect(request.ref)
        assert final.removal_confirmed
        assert final.reason == (
            "expired" if timer == "absolute" else "idle" if timer == "idle" else "disconnected"
        )
        await orchestrator.close()

    asyncio.run(exercise())
