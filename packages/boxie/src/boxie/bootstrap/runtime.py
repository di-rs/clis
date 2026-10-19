"""SDK workers embedded in the orchestrator, with isolated maintenance capacity."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from temporalio.client import Client
from temporalio.worker import Worker

from boxie.bootstrap.schedules import ensure_schedules
from boxie.domain.lifecycle import OrchestratorObservation
from boxie.persistence.outbox import PostgresOutbox
from boxie.persistence.postgres import PostgresLifecycle
from boxie.persistence.postgres_artifacts import PostgresArtifactStore
from boxie.sandbox.execution.orchestrator import Orchestrator
from boxie.temporal.activities import LifecycleActivities, SafeActivityErrors
from boxie.temporal.contracts import TASK_QUEUE, lifecycle_queue, maintenance_queue
from boxie.temporal.dispatcher import CommandDelivery
from boxie.temporal.maintenance import Maintenance, MaintenanceActivities
from boxie.temporal.workflows import SandboxLifecycle


def lifecycle_worker(
    client: Client, activities: LifecycleActivities, *, task_queue: str = TASK_QUEUE
) -> Worker:
    return Worker(
        client,
        task_queue=task_queue,
        workflows=[SandboxLifecycle],
        activities=activities.registered(),
        interceptors=[SafeActivityErrors()],
        max_concurrent_activities=32,
        graceful_shutdown_timeout=timedelta(seconds=25),
    )


@asynccontextmanager
async def embedded_workers(
    client: Client,
    repository: PostgresLifecycle,
    orchestrator: Orchestrator,
    artifacts: PostgresArtifactStore,
) -> AsyncIterator[None]:
    identity = orchestrator.config.identity
    activities = LifecycleActivities(repository, orchestrator)
    delivery = CommandDelivery(
        PostgresOutbox(repository.sessions, identity), client, lifecycle_queue(identity)
    )
    maintenance = MaintenanceActivities(delivery, repository, orchestrator, artifacts)

    async def project(observation: OrchestratorObservation) -> None:
        before = await repository.get(observation.principal, observation.sandbox_id)
        accepted = await repository.record_observation(observation)
        request = await repository.load_request(observation.sandbox_id)
        if not accepted or request.desired_stop or before.state == observation.state:
            return
        await client.get_workflow_handle(f"boxie/sandbox/{observation.sandbox_id}").signal(
            SandboxLifecycle.reconcile, str(observation.event_version)
        )

    orchestrator.observer = project
    try:
        async with (
            lifecycle_worker(client, activities, task_queue=lifecycle_queue(identity)),
            Worker(
                client,
                task_queue=maintenance_queue(identity),
                workflows=[Maintenance],
                activities=[maintenance.maintain],
                interceptors=[SafeActivityErrors()],
                max_concurrent_activities=4,
                graceful_shutdown_timeout=timedelta(seconds=25),
            ),
        ):
            await ensure_schedules(client, identity)
            await orchestrator.compact()
            async with asyncio.TaskGroup() as recovery:
                relay = recovery.create_task(delivery.run())
                try:
                    yield
                finally:
                    relay.cancel()
    finally:
        orchestrator.observer = None
