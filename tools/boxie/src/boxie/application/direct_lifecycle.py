"""PR1 delegates admission and resource ownership to the persistent orchestrator."""

from uuid import UUID

from boxie.application.service import ReadyLifecycle
from boxie.domain.models import Principal, SandboxSpec, SandboxView, TerminationReason


class DirectLifecycle:
    def __init__(self, orchestrator: ReadyLifecycle) -> None:
        self.orchestrator = orchestrator

    async def ready(self) -> bool:
        return await self.orchestrator.ready()

    async def submit(
        self, principal: Principal, idempotency_key: str, spec: SandboxSpec
    ) -> SandboxView:
        return await self.orchestrator.submit(principal, idempotency_key, spec)

    async def get(self, principal: Principal, sandbox_id: UUID) -> SandboxView:
        return await self.orchestrator.get(principal, sandbox_id)

    async def terminate(
        self, principal: Principal, sandbox_id: UUID, reason: TerminationReason
    ) -> SandboxView:
        return await self.orchestrator.terminate(principal, sandbox_id, reason)
