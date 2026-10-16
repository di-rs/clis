"""Idempotent post-commit command delivery and bounded Temporal recovery."""

import asyncio
import logging
from datetime import timedelta
from uuid import UUID

from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from boxie.persistence.outbox import OutboxEvent, PostgresOutbox
from boxie.temporal.contracts import TASK_QUEUE
from boxie.temporal.workflows import SandboxLifecycle

logger = logging.getLogger(__name__)


class CommandDelivery:
    def __init__(self, store: PostgresOutbox, client: Client, task_queue: str = TASK_QUEUE) -> None:
        self.store, self.client, self.task_queue = store, client, task_queue

    async def deliver(self, event: OutboxEvent) -> None:
        # Safe even after Temporal retention removes the old closed execution.
        if await self.store.confirmed_terminal(event.sandbox_id):
            return
        try:
            async with asyncio.timeout(5):
                await self.client.start_workflow(
                    SandboxLifecycle.run,
                    str(event.sandbox_id),
                    id=f"boxie/sandbox/{event.sandbox_id}",
                    task_queue=self.task_queue,
                    id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
                    id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                    start_signal="reconcile",
                    start_signal_args=[str(event.id)],
                    rpc_timeout=timedelta(seconds=5),
                )
        except WorkflowAlreadyStartedError:
            if not await self.store.confirmed_terminal(event.sandbox_id):
                raise RuntimeError("closed workflow retains cleanup obligation") from None

    async def _one(self, event: OutboxEvent, semaphore: asyncio.Semaphore) -> None:
        async with semaphore:
            try:
                await self.deliver(event)
                await self.store.acknowledge(event)
            except Exception:
                logger.warning("command delivery pending: %s", event.id)

    async def recover_once(self) -> None:
        events = await self.store.pending()
        semaphore = asyncio.Semaphore(8)
        async with asyncio.TaskGroup() as batch:
            for event in events:
                batch.create_task(self._one(event, semaphore))

    async def deliver_pending(self, sandbox_id: UUID) -> None:
        try:
            async with asyncio.timeout(1):
                for event in await self.store.pending(sandbox_id):
                    await self.deliver(event)
                    await self.store.acknowledge(event)
        except Exception:
            pass  # Acceptance is already durable; scheduled recovery repairs delivery.
