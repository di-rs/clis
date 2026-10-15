"""Dispatcher task lifetime at the SQL/Temporal boundaries, without real services."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from boxie.persistence.outbox import OutboxEvent, PostgresOutbox
from boxie.temporal.dispatcher import OutboxDispatcher
from temporalio.client import Client


@pytest.mark.parametrize("failure", ["retry_write", "shutdown"])
def test_batch_drains_before_next_claim_and_recovers_unacknowledged(failure: str) -> None:
    async def exercise() -> None:
        events = [
            OutboxEvent(uuid4(), uuid4(), "owner", datetime.now(UTC) + timedelta(seconds=30), 1)
            for _ in range(16)
        ]
        acknowledged: set[UUID] = set()
        claimed: set[UUID] = set()
        retry_failed = asyncio.Event()
        saturated = asyncio.Event()
        release = asyncio.Event()
        settled = 0
        active = peak = claims = 0
        recovering = False

        class Store:
            async def claim(self, owner: str) -> list[OutboxEvent]:
                nonlocal claims
                claims += 1
                pending = [e for e in events if e.id not in claimed | acknowledged]
                claimed.update(e.id for e in pending)
                return pending

            async def confirmed_terminal(self, sandbox_id: UUID) -> bool:
                return False

            async def valid(self, event: OutboxEvent) -> bool:
                return True

            async def acknowledge(self, event: OutboxEvent) -> bool:
                acknowledged.add(event.id)
                return True

            async def retry(self, event: OutboxEvent) -> None:
                retry_failed.set()
                raise OSError("retry write unavailable")

        class RPC:
            async def start_workflow(self, *args: Any, **kwargs: Any) -> None:
                nonlocal active, peak, settled
                active += 1
                peak = max(peak, active)
                if active == 8:
                    saturated.set()
                failed = False
                try:
                    if not recovering and args[1] == str(events[0].sandbox_id):
                        await saturated.wait()
                        if failure == "retry_write":
                            failed = True
                            raise OSError("RPC failed")
                    await release.wait()
                finally:
                    # Even cancellation must await transport shutdown before
                    # another batch can own its own concurrency allowance.
                    if not failed:
                        await release.wait()
                    active -= 1
                    settled += 1

        dispatcher = OutboxDispatcher(cast(PostgresOutbox, Store()), cast(Client, RPC()))
        first = asyncio.create_task(dispatcher.poll())
        second: asyncio.Task[None] | None = None
        try:
            await asyncio.wait_for(saturated.wait(), 1)
            if failure == "shutdown":
                first.cancel()
            if failure == "retry_write":
                await asyncio.wait_for(retry_failed.wait(), 1)
            second = asyncio.create_task(dispatcher.poll())
            await asyncio.sleep(0.05)
            assert not first.done(), "batch escaped while sibling RPCs were active"
            assert claims == 1, "next batch claimed before the prior batch settled"
            assert active <= 8 and peak <= 8
            assert events[0].id not in acknowledged
        finally:
            release.set()
            await asyncio.gather(first, *([second] if second else []), return_exceptions=True)
        assert active == 0 and settled > 0
        assert events[0].id not in acknowledged
        # Simulate lease expiry: all unacknowledged claims remain eligible.
        claimed.clear()
        recovering = True
        await dispatcher.poll()
        assert acknowledged == {e.id for e in events}
        assert active == 0 and peak <= 8

    asyncio.run(exercise())
