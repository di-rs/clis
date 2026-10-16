"""Unprivileged Temporal worker and outbox dispatcher; never owns Docker/PTY state."""

import argparse
import asyncio
import json
import signal
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from service_logging.logging import install_asyncio_handler, log_runtime, run_process

from boxie.temporal.contracts import TASK_QUEUE

if TYPE_CHECKING:
    from temporalio.client import Client
    from temporalio.worker import Worker

    from boxie.temporal.activities import LifecycleActivities


def lifecycle_worker(
    client: Client, activities: LifecycleActivities, *, task_queue: str = TASK_QUEUE
) -> Worker:
    from temporalio.worker import Worker

    from boxie.temporal.activities import SafeActivityErrors
    from boxie.temporal.workflows import SandboxLifecycle

    return Worker(
        client,
        task_queue=task_queue,
        workflows=[SandboxLifecycle],
        activities=activities.registered(),
        interceptors=[SafeActivityErrors()],
        max_concurrent_activities=32,
        graceful_shutdown_timeout=timedelta(seconds=25),
    )


def _main() -> None:
    from boxie.bootstrap.config import read_credential
    from boxie.bootstrap.durable import (
        Dependencies,
        agent_identity,
        durable_config,
        pool_for,
        temporal_client,
    )
    from boxie.observability.runtime import process_metrics
    from boxie.persistence.outbox import PostgresOutbox
    from boxie.persistence.postgres import PostgresLifecycle
    from boxie.sandbox.durable_client import DurableAgentClient
    from boxie.temporal.activities import LifecycleActivities
    from boxie.temporal.dispatcher import OutboxDispatcher

    parser = argparse.ArgumentParser(description="Boxie durable lifecycle worker")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    settings = json.loads(args.config.read_text())
    config = durable_config(settings["durable"])

    async def run() -> None:
        install_asyncio_handler(log_runtime())
        client = await temporal_client(config)
        async with (
            process_metrics("worker") as metrics,
            pool_for(config.postgres_dsn_file) as pool,
            DurableAgentClient(
                settings["agent_url"],
                read_credential(Path(settings["agent_credential_file"])),
                timeout=settings.get("request_timeout", 20),
            ) as agent,
        ):
            repository = PostgresLifecycle(
                pool,
                agent,
                agent_identity(settings["identity"]),
                Dependencies(agent, client),
                diagnostics=metrics.transition,
            )
            activities = LifecycleActivities(repository, agent)
            dispatcher = OutboxDispatcher(PostgresOutbox(pool), client)
            stopped = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stopped.set)
            async with lifecycle_worker(client, activities):
                delivery = asyncio.create_task(dispatcher.run())
                try:
                    while not stopped.is_set():
                        try:
                            await repository.purge_retained()
                        except Exception:
                            pass
                        try:
                            await asyncio.wait_for(stopped.wait(), 30)
                        except TimeoutError:
                            pass
                finally:
                    delivery.cancel()
                    await asyncio.gather(delivery, return_exceptions=True)
                    for sig in (signal.SIGINT, signal.SIGTERM):
                        loop.remove_signal_handler(sig)

    asyncio.run(run())


def main() -> None:
    from boxie.observability.logging import process_logging

    runtime = process_logging("worker")
    run_process(runtime, _main)


if __name__ == "__main__":
    main()
