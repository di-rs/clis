"""Shared explicit SQL/mTLS construction. Deployment migrations remain operator jobs."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from temporalio.api.enums.v1 import TaskQueueType
from temporalio.api.taskqueue.v1 import TaskQueue
from temporalio.api.workflowservice.v1 import DescribeNamespaceRequest, DescribeTaskQueueRequest
from temporalio.client import Client
from temporalio.service import TLSConfig

from boxie.bootstrap.config import DurableConfig, read_credential
from boxie.domain.models import AgentIdentity
from boxie.persistence.postgres import Pool
from boxie.sandbox.durable_client import DurableAgentClient
from boxie.temporal.contracts import TASK_QUEUE


def durable_config(data: dict[str, Any]) -> DurableConfig:
    converted: dict[str, Any] = {
        key: Path(value) if key.endswith("_file") else value for key, value in data.items()
    }
    return DurableConfig(**converted)


def agent_identity(data: dict[str, str]) -> AgentIdentity:
    return AgentIdentity(UUID(data["installation_id"]), data["host_id"])


def pool_for(path: Path) -> Pool:
    return AsyncConnectionPool(
        read_credential(path, allow_spaces=True).decode(),
        open=False,
        min_size=1,
        max_size=16,
        timeout=5,
        kwargs={"row_factory": dict_row},
    )


async def temporal_client(config: DurableConfig) -> Client:
    # No plaintext/test-server alternative is accepted by this composition root.
    return await Client.connect(
        config.temporal_address,
        namespace=config.temporal_namespace,
        tls=TLSConfig(
            server_root_ca_cert=config.temporal_ca_file.read_bytes(),
            domain=config.temporal_server_name,
            client_cert=config.temporal_cert_file.read_bytes(),
            client_private_key=config.temporal_key_file.read_bytes(),
        ),
    )


class Dependencies:
    def __init__(self, agent: DurableAgentClient, temporal: Client) -> None:
        self.agent, self.temporal = agent, temporal

    async def __call__(self) -> bool:
        async with asyncio.timeout(5):
            if (
                not await self.agent.ready()
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
                # A stale poller list is not current worker readiness.
                if not any(
                    (datetime.now(UTC) - p.last_access_time.ToDatetime(tzinfo=UTC)).total_seconds()
                    < 30
                    for p in queue.pollers
                ):
                    return False
            return True
