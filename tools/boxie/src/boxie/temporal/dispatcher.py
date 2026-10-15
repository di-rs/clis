"""At-least-once notification delivery; durable requests are the sole authority."""

import asyncio
import logging
import time
from datetime import timedelta
from uuid import uuid4

from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from boxie.persistence.outbox import OutboxEvent, PostgresOutbox
from boxie.temporal.contracts import TASK_QUEUE
from boxie.temporal.workflows import SandboxLifecycle

logger = logging.getLogger(__name__)


class OutboxDispatcher:
    def __init__(self, store: PostgresOutbox, client: Client, task_queue: str = TASK_QUEUE) -> None:
        self.store, self.client, self.task_queue = store, client, task_queue
        self.owner = str(uuid4())
        self._poll_lock = asyncio.Lock()

    async def deliver(self, event: OutboxEvent) -> None:
        # Safe even after Temporal retention removes the old closed execution.
        if await self.store.confirmed_terminal(event.sandbox_id):
            return
        checked = time.monotonic()
        if not await self.store.valid(event) or time.monotonic() - checked >= 5:
            raise RuntimeError("outbox claim expired")
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
                # Never log upstream exception objects: they may include credentials.
                logger.warning("outbox delivery pending: %s", event.id)
                await self.store.retry(event)

    async def poll(self) -> None:
        async with self._poll_lock:
            events = await self.store.claim(self.owner)
            semaphore = asyncio.Semaphore(8)
            await asyncio.gather(*(self._one(event, semaphore) for event in events))

    async def run(self) -> None:
        while True:
            try:
                await self.poll()
            except Exception:
                logger.warning("outbox persistence unavailable")
            await asyncio.sleep(0.25)
