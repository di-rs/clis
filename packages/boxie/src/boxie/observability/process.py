"""Process-owned telemetry background work, isolated from lifecycle availability."""

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from temporalio.api.enums.v1 import TaskQueueType
from temporalio.api.taskqueue.v1 import TaskQueue
from temporalio.api.workflowservice.v1 import DescribeNamespaceRequest, DescribeTaskQueueRequest

from boxie.observability.metrics import Metrics
from boxie.observability.telemetry.logging import LogDispatcher, run_process
from boxie.observability.telemetry.metrics import MetricsListener
from boxie.temporal.contracts import lifecycle_queue, maintenance_queue

if TYPE_CHECKING:
    from temporalio.client import Client

    from boxie.domain.models import OrchestratorIdentity
    from boxie.persistence.database import SessionFactory
    from boxie.sandbox.execution.orchestrator import Orchestrator
    from boxie.sandbox.transport.client import OrchestratorClient


def run_metrics_process(logs: LogDispatcher, role: str, run: Callable[[Metrics], None]) -> None:
    """Own the private listener beside logging, outside the application's loop."""
    listener: MetricsListener | None = None

    def start() -> None:
        nonlocal listener
        metrics = Metrics(role, logs=logs)
        try:
            listener = MetricsListener(
                metrics,
                os.environ.get("BOXIE_METRICS_HOST", "127.0.0.1"),
                {"api": 9101, "orchestrator": 9103}[role],
            )
        except ValueError:
            logs.configuration_error()
        else:
            listener.start()
        run(metrics)

    def close_metrics(remaining: float) -> None:
        if listener is not None:
            listener.close(remaining)

    run_process(logs, start, close_metrics=close_metrics)


async def _probe(metrics: Metrics, name: str, check: Callable[[], Awaitable[bool]]) -> None:
    try:
        ready = await check()
    except Exception:
        ready = False
    metrics.dependency(name, ready)


async def sql_ready(sessions: SessionFactory) -> bool:
    from sqlmodel import select

    async with sessions() as session:
        await session.exec(select(1))
    return True


async def api_dependencies(
    metrics: Metrics,
    orchestrator: OrchestratorClient,
    temporal: Client,
    identity: OrchestratorIdentity,
) -> None:
    async def namespace() -> bool:
        await temporal.workflow_service.describe_namespace(
            DescribeNamespaceRequest(namespace=temporal.namespace), timeout=timedelta(seconds=5)
        )
        return True

    async def poller(kind: TaskQueueType.ValueType, queue_name: str) -> bool:
        from boxie.bootstrap.dependencies import WORKER_PRESENCE_SECONDS

        queue = await temporal.workflow_service.describe_task_queue(
            DescribeTaskQueueRequest(
                namespace=temporal.namespace,
                task_queue=TaskQueue(name=queue_name),
                task_queue_type=kind,
            ),
            timeout=timedelta(seconds=5),
        )
        return any(
            (datetime.now(UTC) - item.last_access_time.ToDatetime(tzinfo=UTC)).total_seconds()
            < WORKER_PRESENCE_SECONDS
            for item in queue.pollers
        )

    async def workflow() -> bool:
        return await poller(TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW, lifecycle_queue(identity))

    async def activity() -> bool:
        return await poller(TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY, lifecycle_queue(identity))

    async def maintenance_workflow() -> bool:
        return await poller(TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW, maintenance_queue(identity))

    async def maintenance_activity() -> bool:
        return await poller(TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY, maintenance_queue(identity))

    checks = (
        ("orchestrator", orchestrator.ready),
        ("temporal", temporal.service_client.check_health),
        ("namespace", namespace),
        ("workflow_poller", workflow),
        ("activity_poller", activity),
        ("maintenance_workflow_poller", maintenance_workflow),
        ("maintenance_activity_poller", maintenance_activity),
    )
    while True:
        try:
            async with asyncio.timeout(5):
                await asyncio.gather(*(_probe(metrics, name, check) for name, check in checks))
        except TimeoutError:
            pass  # Cancelled probes retain their previous age; unknown is not false.
        await asyncio.sleep(15)


async def orchestrator_cache(metrics: Metrics, orchestrator: Orchestrator) -> None:
    while True:
        remaining = [
            max(0, execution.deadlines.lease - orchestrator.monotonic())
            for identifier, execution in orchestrator.executions.items()
            if execution.deadlines is not None
            and identifier in orchestrator.records
            and orchestrator.records[identifier].state.value
            in ("accepted", "provisioning", "ready")
        ]
        metrics.refresh_lease(len(remaining), min(remaining) if remaining else None)
        metrics.dependency("execution", orchestrator.execution_ready)
        await asyncio.sleep(1)


async def orchestrator_dependencies(
    metrics: Metrics, orchestrator: Orchestrator, sessions: SessionFactory
) -> None:
    async def postgres() -> bool:
        return await sql_ready(sessions)

    while True:
        try:
            async with asyncio.timeout(5):
                await asyncio.gather(
                    _probe(metrics, "postgres", postgres),
                    _probe(metrics, "docker", orchestrator.docker.ready),
                )
        except TimeoutError:
            pass
        await asyncio.sleep(15)


@asynccontextmanager
async def background(*operations: Coroutine[Any, Any, None]) -> AsyncIterator[None]:
    tasks = [asyncio.create_task(operation) for operation in operations]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
