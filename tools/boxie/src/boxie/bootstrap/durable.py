"""Shared explicit SQL/mTLS construction. Deployment migrations remain operator jobs."""

import asyncio
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from temporalio.api.enums.v1 import TaskQueueType
from temporalio.api.taskqueue.v1 import TaskQueue
from temporalio.api.workflowservice.v1 import DescribeNamespaceRequest, DescribeTaskQueueRequest
from temporalio.client import Client
from temporalio.service import TLSConfig

from boxie.bootstrap.config import DurableConfig, read_credential
from boxie.domain.models import OrchestratorIdentity
from boxie.persistence.database import SessionFactory, database
from boxie.sandbox.transport.client import OrchestratorClient
from boxie.temporal.contracts import TASK_QUEUE

# Temporal 1.32.0's matching long poll defaults to 60 seconds. Poller access
# timestamps refresh between polls, so allow another 30 seconds for scheduling
# and network delay. Revisit this bound if the production server setting changes.
WORKER_PRESENCE_SECONDS = 90


def durable_config(data: dict[str, Any]) -> DurableConfig:
    converted: dict[str, Any] = {
        key: Path(value) if key.endswith("_file") else value for key, value in data.items()
    }
    return DurableConfig(**converted)


def orchestrator_identity(data: dict[str, str]) -> OrchestratorIdentity:
    return OrchestratorIdentity(UUID(data["installation_id"]), data["host_id"])


def database_for(path: Path) -> AbstractAsyncContextManager[SessionFactory]:
    return database(read_credential(path, allow_spaces=True).decode())


async def temporal_client(config: DurableConfig, *, lazy: bool = False) -> Client:
    # No plaintext/test-server alternative is accepted by this composition root.
    return await Client.connect(
        config.temporal_address,
        namespace=config.temporal_namespace,
        lazy=lazy,
        tls=TLSConfig(
            server_root_ca_cert=config.temporal_ca_file.read_bytes(),
            domain=config.temporal_server_name,
            client_cert=config.temporal_cert_file.read_bytes(),
            client_private_key=config.temporal_key_file.read_bytes(),
        ),
    )


class Dependencies:
    def __init__(self, orchestrator: OrchestratorClient, temporal: Client) -> None:
        self.orchestrator, self.temporal = orchestrator, temporal

    async def __call__(self) -> bool:
        async with asyncio.timeout(5):
            if (
                not await self.orchestrator.ready()
                or not await self.temporal.service_client.check_health()
            ):
                return False
            await self.temporal.workflow_service.describe_namespace(
                DescribeNamespaceRequest(namespace=self.temporal.namespace),
                timeout=timedelta(seconds=5),
            )
            for kind in (
                TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW,
                TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY,
            ):
                queue = await self.temporal.workflow_service.describe_task_queue(
                    DescribeTaskQueueRequest(
                        namespace=self.temporal.namespace,
                        task_queue=TaskQueue(name=TASK_QUEUE),
                        task_queue_type=kind,
                    ),
                    timeout=timedelta(seconds=5),
                )
                # Recent presence is bounded evidence, not instantaneous liveness.
                if not any(
                    (datetime.now(UTC) - p.last_access_time.ToDatetime(tzinfo=UTC)).total_seconds()
                    < WORKER_PRESENCE_SECONDS
                    for p in queue.pollers
                ):
                    return False
            return True
