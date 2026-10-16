"""Activity-owned I/O: sensitive requests never cross the workflow boundary."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlmodel import col, select
from temporalio import activity
from temporalio.exceptions import ApplicationError
from temporalio.worker import ActivityInboundInterceptor, ExecuteActivityInput, Interceptor

from boxie.domain.errors import BoxieError
from boxie.domain.lifecycle import (
    DurableRequest,
    EnsureRequest,
    ExpiryRequest,
    OrchestratorObservation,
    SandboxRef,
)
from boxie.domain.models import SandboxState, TerminationReason
from boxie.persistence.models import OrchestratorObservationRecord, Sandbox
from boxie.persistence.postgres import PostgresLifecycle
from boxie.sandbox.durable_client import DurableOrchestratorClient
from boxie.temporal.contracts import LifecycleFacts, StopRequest


class SafeActivityInbound(ActivityInboundInterceptor):
    async def execute_activity(self, input: ExecuteActivityInput) -> Any:
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
            # Sanitize before SDK logging, including nested context and details.
            raise ApplicationError(
                "lifecycle operation pending" if not non_retryable else "lifecycle request stopped",
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
    def __init__(
        self, repository: PostgresLifecycle, orchestrator: DurableOrchestratorClient
    ) -> None:
        self.repository, self.orchestrator = repository, orchestrator

    async def _request(self, sandbox_id: str) -> DurableRequest:
        return await self.repository.load_request(UUID(sandbox_id))

    async def _bind(self, request: DurableRequest) -> UUID:
        boot = await self.orchestrator.boot(request.identity)
        if not await self.repository.bind_orchestrator_boot(
            request.principal,
            request.identity,
            request.sandbox_id,
            request.generation,
            request.orchestrator_boot_id,
            boot,
        ):
            raise BoxieError(409, "orchestrator boot changed")
        return boot

    async def _record(self, observation: OrchestratorObservation) -> None:
        await self.repository.record_observation(observation)

    @activity.defn
    async def load_request(self, sandbox_id: str) -> LifecycleFacts:
        request = await self._request(sandbox_id)
        async with self.repository.sessions() as session:
            row, observation = (
                await session.exec(
                    select(Sandbox, OrchestratorObservationRecord)
                    .outerjoin(
                        OrchestratorObservationRecord,
                        col(OrchestratorObservationRecord.sandbox_id) == col(Sandbox.id),
                    )
                    .where(Sandbox.id == request.sandbox_id)
                )
            ).one()
        deadlines = [request.absolute_deadline]
        if observation is not None and observation.ready_at:
            deadlines.append(
                (observation.last_input_at or observation.ready_at)
                + timedelta(seconds=request.spec.policy.idle_seconds)
            )
        if observation is not None and observation.disconnected_at:
            deadlines.append(
                observation.disconnected_at
                + timedelta(seconds=request.spec.policy.disconnect_seconds)
            )
        return LifecycleFacts(
            request.absolute_deadline.timestamp(),
            request.desired_stop or row.state in ("terminating", "terminated", "failed"),
            row.cleaned_at is not None,
            min(deadlines).timestamp(),
        )

    @activity.defn
    async def ensure_sandbox(self, sandbox_id: str) -> None:
        request = await self._request(sandbox_id)
        if request.desired_stop:
            return
        boot = await self._bind(request)
        observation = await self.orchestrator.ensure_execution(
            EnsureRequest.from_durable(request), boot
        )
        await self._record(observation)
        while observation.state in (SandboxState.ACCEPTED, SandboxState.PROVISIONING):
            activity.heartbeat()
            await asyncio.sleep(1)
            observation = await self.orchestrator.inspect_execution(request, boot)
            await self._record(observation)
        if observation.state != SandboxState.READY:
            raise ApplicationError(
                "provisioning stopped", type="ProvisioningStopped", non_retryable=True
            )

    @activity.defn
    async def inspect_and_renew(self, sandbox_id: str) -> LifecycleFacts:
        request = await self._request(sandbox_id)
        if (await self.load_request(sandbox_id)).cleaned:
            return await self.load_request(sandbox_id)
        boot = await self._bind(request)
        try:
            observation = await self.orchestrator.inspect_execution(request, boot)
        except BoxieError as error:
            if error.status != 404:
                raise
            # Provision has not registered yet; only ensure may initiate it.
            return await self.load_request(sandbox_id)
        if not request.desired_stop and observation.state in (
            SandboxState.ACCEPTED,
            SandboxState.PROVISIONING,
            SandboxState.READY,
        ):
            try:
                observation = await self.orchestrator.renew_execution(request, boot)
            except BoxieError as error:
                if error.status != 409:
                    raise
                observation = await self.orchestrator.inspect_execution(request, boot)
        await self._record(observation)
        return await self.load_request(sandbox_id)

    @activity.defn
    async def expire_if_current(self, sandbox_id: str) -> LifecycleFacts:
        request = await self._request(sandbox_id)
        now = datetime.now(UTC)
        if now >= request.absolute_deadline:
            await self.repository.terminate(
                request.principal, request.sandbox_id, TerminationReason.EXPIRED
            )
        else:
            boot = await self._bind(request)
            observation = await self.orchestrator.inspect_execution(request, boot)
            choices: list[tuple[datetime, TerminationReason]] = []
            if observation.ready_at:
                choices.append(
                    (
                        (observation.last_input_at or observation.ready_at)
                        + timedelta(seconds=request.spec.policy.idle_seconds),
                        TerminationReason.IDLE,
                    )
                )
            if observation.disconnected_at:
                choices.append(
                    (
                        observation.disconnected_at
                        + timedelta(seconds=request.spec.policy.disconnect_seconds),
                        TerminationReason.DISCONNECTED,
                    )
                )
            if choices:
                deadline, reason = min(choices, key=lambda item: item[0])
                if now >= deadline:
                    observation = await self.orchestrator.expire_execution(
                        request,
                        ExpiryRequest(
                            SandboxRef(request.sandbox_id, request.generation),
                            reason,
                            observation.event_version,
                            boot,
                        ),
                    )
            await self._record(observation)
        return await self.load_request(sandbox_id)

    @activity.defn
    async def request_stop(self, request: StopRequest) -> None:
        durable = await self._request(request.sandbox_id)
        await self.repository.terminate(
            durable.principal, durable.sandbox_id, TerminationReason(request.reason)
        )

    @activity.defn
    async def remove_sandbox(self, sandbox_id: str) -> None:
        if (await self.load_request(sandbox_id)).cleaned:
            return
        request = await self._request(sandbox_id)
        if not request.desired_stop:
            raise BoxieError(409, "durable stop required before removal")
        boot = await self._bind(request)
        try:
            observation = await self.orchestrator.remove_execution(request, boot)
        except BoxieError as error:
            if error.status != 404:
                raise
            # Initialize a fenced absent identity. Durable stop is already committed;
            # the orchestrator-owned SQL guard forbids create/start even if this RPC is lost.
            await self.orchestrator.ensure_execution(EnsureRequest.from_durable(request), boot)
            observation = await self.orchestrator.remove_execution(request, boot)
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
            self.expire_if_current,
            self.request_stop,
            self.remove_sandbox,
        ]
