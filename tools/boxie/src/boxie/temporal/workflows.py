"""One deterministic run chain per admitted sandbox, including unbounded cleanup."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, is_cancelled_exception

from boxie.temporal.contracts import LifecycleFacts, LifecycleResult, StopRequest

with workflow.unsafe.imports_passed_through():
    from boxie.temporal.activities import LifecycleActivities

PROVISION_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=5,
)
CLEANUP_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=0,
)


@workflow.defn
class SandboxLifecycle:
    def __init__(self) -> None:
        self.dirty = True

    @workflow.signal
    def reconcile(self, event_id: str) -> None:
        # Notifications carry no authority and need no unbounded deduplication set.
        self.dirty = True

    async def _facts(
        self,
        method: Callable[[LifecycleActivities, str], Awaitable[LifecycleFacts]],
        sandbox_id: str,
    ) -> LifecycleFacts:
        try:
            return await workflow.execute_activity_method(
                method,
                sandbox_id,
                start_to_close_timeout=timedelta(seconds=20),
                schedule_to_close_timeout=timedelta(seconds=30),
                retry_policy=CLEANUP_RETRY,
            )
        except ActivityError as error:
            # The SDK wraps cancellation of an awaited activity. Do not treat it
            # as a transient dependency failure and lose the workflow stop request.
            if is_cancelled_exception(error):
                raise asyncio.CancelledError from None
            raise

    async def _cleanup(self, sandbox_id: str, reason: str) -> LifecycleResult:
        # Never enter absence initialization or deletion until stop commits to SQL.
        # No schedule-to-close deadline can end this obligation. Native server
        # retries keep one history command per operation, regardless of outage length.
        await workflow.execute_activity_method(
            LifecycleActivities.request_stop,
            StopRequest(sandbox_id, reason),
            start_to_close_timeout=timedelta(seconds=20),
            retry_policy=CLEANUP_RETRY,
            cancellation_type=workflow.ActivityCancellationType.ABANDON,
        )
        await workflow.execute_activity_method(
            LifecycleActivities.remove_sandbox,
            sandbox_id,
            start_to_close_timeout=timedelta(seconds=20),
            retry_policy=CLEANUP_RETRY,
            cancellation_type=workflow.ActivityCancellationType.ABANDON,
        )
        # Removal succeeds only after fenced physical absence is persisted with
        # accounting release. Repeating a separate confirmation adds no authority.
        await workflow.wait_condition(workflow.all_handlers_finished)
        return LifecycleResult(cleaned=True)

    @workflow.run
    async def run(self, sandbox_id: str) -> LifecycleResult:
        provision: workflow.ActivityHandle[None] | None = None
        reason = "requested"
        next_inspection = workflow.now().timestamp()
        provision_settled = False
        try:
            while True:
                self.dirty = False  # Clear BEFORE awaiting: never erase a racing signal.
                try:
                    facts = await self._facts(LifecycleActivities.load_request, sandbox_id)
                except ActivityError:
                    await workflow.sleep(timedelta(seconds=30))
                    continue
                if facts.cleaned:
                    return LifecycleResult(cleaned=True)
                if facts.desired_stop:
                    break
                if provision is None:
                    provision = workflow.start_activity_method(
                        LifecycleActivities.ensure_sandbox,
                        sandbox_id,
                        start_to_close_timeout=timedelta(seconds=30),
                        schedule_to_close_timeout=timedelta(seconds=120),
                        heartbeat_timeout=timedelta(seconds=10),
                        retry_policy=PROVISION_RETRY,
                        cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                    )
                if provision.done() and not provision_settled:
                    try:
                        await provision
                        provision_settled = True
                    except ActivityError as error:
                        if is_cancelled_exception(error):
                            raise asyncio.CancelledError from None
                        reason = "provisioning_failed"
                        break
                deadline = min(
                    facts.absolute_deadline, facts.next_deadline or facts.absolute_deadline
                )
                if workflow.now().timestamp() >= deadline:
                    try:
                        await workflow.execute_activity_method(
                            LifecycleActivities.request_stop,
                            StopRequest(sandbox_id, "expired"),
                            start_to_close_timeout=timedelta(seconds=20),
                            retry_policy=CLEANUP_RETRY,
                        )
                        facts = await self._facts(LifecycleActivities.load_request, sandbox_id)
                        if facts.desired_stop:
                            break
                    except ActivityError as error:
                        if is_cancelled_exception(error):
                            raise asyncio.CancelledError from None
                if workflow.now().timestamp() >= next_inspection:
                    next_inspection = workflow.now().timestamp() + 30
                    try:
                        facts = await self._facts(LifecycleActivities.inspect_and_renew, sandbox_id)
                        if facts.desired_stop or facts.cleaned:
                            break
                    except ActivityError:
                        pass
                deadline = min(
                    facts.absolute_deadline, facts.next_deadline or facts.absolute_deadline
                )
                delay = max(0.1, min(next_inspection, deadline) - workflow.now().timestamp())

                def wake(
                    pending: workflow.ActivityHandle[None] | None = provision,
                    settled: bool = provision_settled,
                ) -> bool:
                    return self.dirty or (pending is not None and pending.done() and not settled)

                try:
                    await workflow.wait_condition(wake, timeout=timedelta(seconds=delay))
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            # Cancellation is a stop request, never permission to abandon a resource.
            reason = "requested"
        finally:
            if provision is not None and not provision.done():
                provision.cancel()
                try:
                    await provision
                except asyncio.CancelledError, ActivityError:
                    pass
        cleanup = asyncio.create_task(self._cleanup(sandbox_id, reason))
        try:
            return await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            return await cleanup
