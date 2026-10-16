"""Unprivileged Temporal worker and outbox dispatcher; never owns Docker/PTY state."""

import argparse
import asyncio
import json
import signal
from datetime import timedelta
from pathlib import Path

from temporalio.client import Client
from temporalio.worker import Worker

from boxie.bootstrap.config import read_credential
from boxie.bootstrap.durable import (
    Dependencies,
    database_for,
    durable_config,
    orchestrator_identity,
    temporal_client,
)
from boxie.persistence.outbox import PostgresOutbox
from boxie.persistence.postgres import PostgresLifecycle
from boxie.sandbox.durable_client import DurableOrchestratorClient
from boxie.temporal.activities import LifecycleActivities, SafeActivityErrors
from boxie.temporal.contracts import TASK_QUEUE
from boxie.temporal.dispatcher import OutboxDispatcher
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Boxie durable lifecycle worker")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    settings = json.loads(args.config.read_text())
    config = durable_config(settings["durable"])

    async def run() -> None:
        client = await temporal_client(config)
        async with (
            database_for(config.postgres_dsn_file) as sessions,
            DurableOrchestratorClient(
                settings["orchestrator_url"],
                read_credential(Path(settings["orchestrator_credential_file"])),
                timeout=settings.get("request_timeout", 20),
            ) as orchestrator,
        ):
            repository = PostgresLifecycle(
                sessions,
                orchestrator,
                orchestrator_identity(settings["identity"]),
                Dependencies(orchestrator, client),
            )
            activities = LifecycleActivities(repository, orchestrator)
            dispatcher = OutboxDispatcher(PostgresOutbox(sessions), client)
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


if __name__ == "__main__":
    main()
