"""A failed command cannot block the rest of a recoverable delivery batch."""

import asyncio
from typing import Any, cast
from uuid import uuid4

from boxie.persistence.outbox import OutboxEvent, PostgresOutbox
from boxie.temporal.dispatcher import CommandDelivery
from temporalio.client import Client


def test_failed_delivery_stays_pending_while_other_commands_complete() -> None:
    async def exercise() -> None:
        pending = [OutboxEvent(uuid4(), uuid4()) for _ in range(10)]
        failed = pending[0]
        unavailable = True

        class Store:
            async def pending(self, *args: Any) -> list[OutboxEvent]:
                return list(pending)

            async def confirmed_terminal(self, *args: Any) -> bool:
                return False

            async def acknowledge(self, event: OutboxEvent) -> bool:
                if event in pending:
                    pending.remove(event)
                return True

        class RPC:
            async def start_workflow(self, *args: Any, **kwargs: Any) -> None:
                if unavailable and args[1] == str(failed.sandbox_id):
                    raise OSError("Temporal unavailable")

        delivery = CommandDelivery(cast(PostgresOutbox, Store()), cast(Client, RPC()))
        await delivery.recover_once()
        assert pending == [failed]
        unavailable = False
        await delivery.recover_once()
        assert pending == []

    asyncio.run(exercise())
