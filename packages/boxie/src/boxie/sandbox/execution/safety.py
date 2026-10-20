"""Local deadline and journal enforcement, independent of Temporal scheduling."""

import asyncio
from typing import TYPE_CHECKING

from boxie.domain.models import SandboxState

if TYPE_CHECKING:
    from boxie.sandbox.execution.supervisor import Supervisor


class ExecutionSafety:
    def __init__(self, owner: Supervisor) -> None:
        self.owner = owner
        self.task: asyncio.Task[None] | None = None

    async def inspect(self) -> None:
        owner = self.owner
        for record in list(owner.records.values()):
            execution = owner.executions[record.id]
            if record.state in {SandboxState.TERMINATED, SandboxState.FAILED}:
                continue
            if owner._expiry(record) is not None:
                execution.safety_required = True
            if execution.safety_required and (
                execution.reconciliation is None or execution.reconciliation.done()
            ):
                execution.reconciliation = asyncio.create_task(owner._supervised_reconcile(record))
        # Startup cleanup must fence old executions before serving requests.
        await asyncio.gather(
            *(r.reconciliation for r in owner.executions.values() if r.reconciliation is not None)
        )

    def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self._run())

    async def close(self) -> None:
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def _run(self) -> None:
        owner = self.owner
        while True:
            if not owner._journal_healthy or (
                owner._input_flush_at is not None and owner.monotonic() >= owner._input_flush_at
            ):
                try:
                    owner._save()
                except Exception:
                    pass
            # Each bounded removal is independent; one slow Docker operation must
            # not delay enforcement of another sandbox's deadline.
            for record in list(owner.records.values()):
                execution = owner.executions[record.id]
                if record.state in {SandboxState.TERMINATED, SandboxState.FAILED}:
                    continue
                if owner._expiry(record) is not None:
                    execution.safety_required = True
                if execution.safety_required and (
                    execution.reconciliation is None or execution.reconciliation.done()
                ):
                    execution.reconciliation = asyncio.create_task(
                        owner._supervised_reconcile(record)
                    )
            await asyncio.sleep(min(1, owner.config.sweep_seconds))
