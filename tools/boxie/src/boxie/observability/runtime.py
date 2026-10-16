"""Process-owned telemetry background work, isolated from lifecycle availability."""

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from service_logging.logging import LogRuntime, run_process
from service_logging.metrics import MetricsListener
from temporalio.api.enums.v1 import TaskQueueType
from temporalio.api.taskqueue.v1 import TaskQueue
from temporalio.api.workflowservice.v1 import DescribeNamespaceRequest, DescribeTaskQueueRequest

from boxie.observability.metrics import Metrics
from boxie.temporal.contracts import TASK_QUEUE

if TYPE_CHECKING:
    from temporalio.client import Client

    from boxie.persistence.postgres import Pool
    from boxie.sandbox.durable_client import DurableAgentClient
    from boxie.sandbox.lifecycle import DurableAgent


def run_metrics_process(logs: LogRuntime, role: str, run: Callable[[Metrics], None]) -> None:
    """Own the private listener beside logging, outside the application's loop."""
    listener: MetricsListener | None = None

    def start() -> None:
        nonlocal listener
        metrics = Metrics(role, logs=logs)
        listener = MetricsListener(
            metrics,
            os.environ.get("BOXIE_METRICS_HOST", "127.0.0.1"),
            {"api": 9101, "worker": 9102, "agent": 9103}[role],
        )
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


async def sql_ready(pool: Pool) -> bool:
    async with pool.connection(timeout=0.5) as conn:
        await conn.execute("SELECT 1")
    return True


async def api_dependencies(metrics: Metrics, agent: DurableAgentClient, temporal: Client) -> None:
    async def namespace() -> bool:
        await temporal.workflow_service.describe_namespace(
            DescribeNamespaceRequest(namespace=temporal.namespace), timeout=timedelta(seconds=5)
        )
        return True

    async def poller(kind: TaskQueueType.ValueType) -> bool:
        from boxie.bootstrap.durable import WORKER_PRESENCE_SECONDS

        queue = await temporal.workflow_service.describe_task_queue(
            DescribeTaskQueueRequest(
                namespace=temporal.namespace,
                task_queue=TaskQueue(name=TASK_QUEUE),
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
        return await poller(TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW)

    async def activity() -> bool:
        return await poller(TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY)

    checks = (
        ("agent", agent.ready),
        ("temporal", temporal.service_client.check_health),
        ("namespace", namespace),
        ("workflow_poller", workflow),
        ("activity_poller", activity),
    )
    while True:
        try:
            async with asyncio.timeout(5):
                await asyncio.gather(*(_probe(metrics, name, check) for name, check in checks))
        except TimeoutError:
            pass  # Cancelled probes retain their previous age; unknown is not false.
        await asyncio.sleep(15)


async def agent_cache(metrics: Metrics, agent: DurableAgent) -> None:
    while True:
        remaining = [
            max(0, deadline.lease - agent.monotonic())
            for identifier, deadline in agent.deadlines.items()
            if identifier in agent.records
            and agent.records[identifier].state.value in ("accepted", "provisioning", "ready")
        ]
        metrics.refresh_lease(len(remaining), min(remaining) if remaining else None)
        metrics.dependency("execution", agent.execution_ready)
        await asyncio.sleep(1)


async def agent_dependencies(metrics: Metrics, agent: DurableAgent, pool: Pool) -> None:
    async def postgres() -> bool:
        return await sql_ready(pool)

    while True:
        try:
            async with asyncio.timeout(5):
                await asyncio.gather(
                    _probe(metrics, "postgres", postgres),
                    _probe(metrics, "docker", agent.docker.ready),
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
