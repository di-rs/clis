"""Activity-owned I/O: sensitive requests never cross the workflow boundary."""

import asyncio
from typing import Any
from uuid import UUID

from sqlmodel import select
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.worker import ActivityInboundInterceptor, ExecuteActivityInput, Interceptor

from boxie.domain.errors import BoxieError
from boxie.domain.lifecycle import (
    EnsureRequest,
    SandboxRef,
    SandboxRequest,
    SupervisorObservation,
)
from boxie.domain.models import SandboxState, TerminationReason
from boxie.observability.context import bind_context
from boxie.observability.events import Transition, deliver, emit
from boxie.persistence.models import Sandbox
from boxie.persistence.postgres import PostgresLifecycle
from boxie.sandbox.execution.supervisor import Supervisor
from boxie.temporal.contracts import LifecycleFacts, StopRequest


class SafeActivityInbound(ActivityInboundInterceptor):
    async def execute_activity(self, input: ExecuteActivityInput) -> Any:
        info = activity.info()
        with bind_context(
            workflow_id=info.workflow_id,
            run_id=info.workflow_run_id,
            activity_attempt=info.attempt,
        ):

            async def heartbeat() -> None:
                while True:
                    activity.heartbeat()
                    await asyncio.sleep(2)

            pulse = asyncio.create_task(heartbeat()) if activity.info().heartbeat_timeout else None
            try:
                return await self.next.execute_activity(input)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                non_retryable = isinstance(error, BoxieError) and error.status in {400, 404, 422}
                category = "InvalidRequest" if non_retryable else "DependencyUnavailable"
                if isinstance(error, ApplicationError) and error.type in {
                    "InvalidRequest",
                    "ProvisioningStopped",
                    "CleanupPending",
                    "DependencyUnavailable",
                }:
                    category = error.type
                    non_retryable = error.non_retryable
                # Cleanup has no terminal failure: accepted ownership must survive
                # even malformed or temporarily missing dependency state.
                if activity.info().activity_type in {"request_stop", "remove_sandbox"}:
                    non_retryable = False
                deliver(
                    emit, Transition(event="activity.failed", reason=category, outcome="failed")
                )
                # Sanitize before SDK logging, including nested context and details.
                raise ApplicationError(
                    "lifecycle operation pending"
                    if not non_retryable
                    else "lifecycle request stopped",
                    type=category,
                    non_retryable=non_retryable,
                ) from None
            finally:
                if pulse:
                    pulse.cancel()
                    await asyncio.gather(pulse, return_exceptions=True)


class SafeActivityErrors(Interceptor):
    def intercept_activity(self, next: ActivityInboundInterceptor) -> ActivityInboundInterceptor:
        return SafeActivityInbound(next)


class LifecycleActivities:
    def __init__(self, repository: PostgresLifecycle, supervisor: Supervisor) -> None:
        self.repository, self.supervisor = repository, supervisor

    async def _request(self, sandbox_id: str) -> SandboxRequest:
        return await self.repository.load_request(UUID(sandbox_id))

    async def _bind(self, request: SandboxRequest) -> UUID:
        if request.identity != self.supervisor.config.identity:
            raise BoxieError(404, "supervisor identity not found")
        boot = self.supervisor.boot_id
        if not await self.repository.bind_supervisor_boot(
            request.principal,
            request.identity,
            request.sandbox_id,
            request.generation,
            request.supervisor_boot_id,
            boot,
        ):
            raise BoxieError(409, "supervisor boot changed")
        return boot

    async def _record(self, observation: SupervisorObservation) -> None:
        await self.repository.record_observation(observation)

    @activity.defn
    async def load_request(self, sandbox_id: str) -> LifecycleFacts:
        request = await self._request(sandbox_id)
        async with self.repository.sessions() as session:
            row = (
                await session.exec(select(Sandbox).where(Sandbox.id == request.sandbox_id))
            ).one()
        if row.cleaned_at is not None and request.sandbox_id in self.supervisor.records:
            # Every workflow completion path reads facts; retries retain late session ownership.
            await self.supervisor.compact()
            if request.sandbox_id in self.supervisor.records:
                raise ApplicationError("journal cleanup pending", type="CleanupPending")
        return LifecycleFacts(
            absolute_deadline=request.absolute_deadline.timestamp(),
            desired_stop=request.desired_stop
            or row.state in ("terminating", "terminated", "failed"),
            cleaned=row.cleaned_at is not None,
        )

    @activity.defn
    async def ensure_sandbox(self, sandbox_id: str) -> None:
        request = await self._request(sandbox_id)
        if request.desired_stop:
            return
        await self._bind(request)
        observation = await self.supervisor.ensure_ready(EnsureRequest.from_request(request))
        await self._record(observation)
        if observation.state != SandboxState.READY:
            raise ApplicationError(
                "provisioning stopped", type="ProvisioningStopped", non_retryable=True
            )

    @activity.defn
    async def inspect_and_renew(self, sandbox_id: str) -> LifecycleFacts:
        request = await self._request(sandbox_id)
        facts = await self.load_request(sandbox_id)
        if facts.cleaned:
            return facts
        boot = await self._bind(request)
        ref = SandboxRef(request.sandbox_id, request.generation)
        try:
            observation = await self.supervisor.inspect(ref)
        except BoxieError as error:
            if error.status != 404:
                raise
            return facts
        record = self.supervisor.records[ref.id]
        if self.supervisor._expiry(record) is not None:
            await self.supervisor._supervised_reconcile(record)
            observation = await self.supervisor.inspect(ref)
        if not request.desired_stop and observation.state in (
            SandboxState.ACCEPTED,
            SandboxState.PROVISIONING,
            SandboxState.READY,
        ):
            try:
                observation = await self.supervisor.renew_control_lease(ref, boot)
            except BoxieError as error:
                if error.status != 409:
                    raise
                observation = await self.supervisor.inspect(ref)
        if observation.reason is not None:
            await self.repository.terminate(
                request.principal, request.sandbox_id, observation.reason
            )
        await self._record(observation)
        return await self.load_request(sandbox_id)

    @activity.defn
    async def request_stop(self, request: StopRequest) -> None:
        sandbox = await self._request(request.sandbox_id)
        await self.repository.terminate(
            sandbox.principal,
            sandbox.sandbox_id,
            # Recorded activity inputs retain their original reason across retries.
            TerminationReason(
                {
                    "orchestrator_restart": "supervisor_restart",
                    "orchestrator_shutdown": "supervisor_shutdown",
                }.get(request.reason, request.reason)
            ),
        )

    @activity.defn
    async def remove_sandbox(self, sandbox_id: str) -> None:
        if (await self.load_request(sandbox_id)).cleaned:
            return
        request = await self._request(sandbox_id)
        if not request.desired_stop:
            raise BoxieError(409, "stop must be committed to SQL before removal")
        await self._bind(request)
        await self.supervisor.initialize(EnsureRequest.from_request(request))
        observation = await self.supervisor.remove(
            SandboxRef(request.sandbox_id, request.generation),
            request.reason or TerminationReason.REQUESTED,
        )
        await self._record(observation)
        # A racing boot transition or newer observation can reject the report.
        # Activity completion must prove committed SQL finality, not only RPC success.
        if not (await self.load_request(sandbox_id)).cleaned:
            raise ApplicationError("physical cleanup pending", type="CleanupPending")

    def registered(self) -> list[Any]:
        return [
            self.load_request,
            self.ensure_sandbox,
            self.inspect_and_renew,
            self.request_stop,
            self.remove_sandbox,
        ]
