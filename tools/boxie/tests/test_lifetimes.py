"""Migrated clock-step assertions against the durable execution owner."""

import asyncio
from pathlib import Path

import pytest
from boxie.domain.models import SandboxState
from test_agent_lifecycle import settled, setup


@pytest.mark.parametrize("wall_step", [-7200, 7200])
@pytest.mark.parametrize("timer", ["initial", "disconnected", "idle", "absolute"])
def test_live_deadlines_ignore_wall_steps(tmp_path: Path, wall_step: int, timer: str) -> None:
    async def exercise() -> None:
        agent, request, daemon, clock, _ = await setup(tmp_path)
        await settled(agent, request.ref, SandboxState.READY)
        epoch = None
        if timer != "initial":
            epoch = await agent.attach(request.ref, attachment_order=1)
        if timer == "disconnected":
            assert epoch is not None
            await agent.detach(request.ref, epoch)
        clock.advance_wall(wall_step)
        await agent.reconcile()
        assert not daemon.deleted_ids
        duration = 3600 if timer == "absolute" else 600 if timer == "idle" else 120
        for _ in range(duration // 30 - 1):
            clock.advance_monotonic(30)
            await agent.renew_control_lease(request.ref, agent.boot_id)
            if timer == "absolute":
                assert epoch is not None
                await agent.write(request.ref, epoch, b"active")
        clock.advance_monotonic(29)
        await agent.reconcile()
        assert not daemon.deleted_ids
        assert (await agent.inspect(request.ref)).absolute_deadline == request.absolute_deadline
        clock.advance_monotonic(1)
        await agent.reconcile()
        final = await agent.inspect(request.ref)
        assert final.removal_confirmed
        assert final.reason == (
            "expired" if timer == "absolute" else "idle" if timer == "idle" else "disconnected"
        )
        await agent.close()

    asyncio.run(exercise())
