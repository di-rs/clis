"""PR1 delegates admission and resource ownership to the persistent agent."""

from uuid import UUID

from boxie.application.service import ReadyLifecycle
from boxie.domain.models import Principal, SandboxSpec, SandboxView, TerminationReason


class DirectLifecycle:
    def __init__(self, agent: ReadyLifecycle) -> None:
        self.agent = agent

    async def ready(self) -> bool:
        return await self.agent.ready()

    async def submit(
        self, principal: Principal, idempotency_key: str, spec: SandboxSpec
    ) -> SandboxView:
        return await self.agent.submit(principal, idempotency_key, spec)

    async def get(self, principal: Principal, sandbox_id: UUID) -> SandboxView:
        return await self.agent.get(principal, sandbox_id)

    async def terminate(
        self, principal: Principal, sandbox_id: UUID, reason: TerminationReason
    ) -> SandboxView:
        return await self.agent.terminate(principal, sandbox_id, reason)
