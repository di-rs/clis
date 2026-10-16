"""Worker presence must require both queues without rejecting an ordinary long poll."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from boxie.bootstrap.durable import Dependencies
from boxie.sandbox.durable_client import DurableAgentClient
from google.protobuf.timestamp_pb2 import Timestamp
from temporalio.api.taskqueue.v1 import PollerInfo
from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest, DescribeTaskQueueResponse
from temporalio.client import Client


@pytest.mark.parametrize("age", [None, 60, 89, 91])
@pytest.mark.parametrize("queue", [1, 2])
def test_each_queue_requires_recent_worker_presence(age: int | None, queue: int) -> None:
    async def exercise() -> None:
        async def describe(
            request: DescribeTaskQueueRequest, **kwargs: object
        ) -> DescribeTaskQueueResponse:
            kind = request.task_queue_type
            selected_age = age if kind == queue else 1
            if selected_age is None:
                return DescribeTaskQueueResponse()
            timestamp = Timestamp()
            timestamp.FromDatetime(datetime.now(UTC) - timedelta(seconds=selected_age))
            return DescribeTaskQueueResponse(pollers=[PollerInfo(last_access_time=timestamp)])

        agent = SimpleNamespace(ready=AsyncMock(return_value=True))
        temporal = SimpleNamespace(
            namespace="boxie",
            service_client=SimpleNamespace(check_health=AsyncMock(return_value=True)),
            workflow_service=SimpleNamespace(
                describe_namespace=AsyncMock(), describe_task_queue=describe
            ),
        )
        assert await Dependencies(cast(DurableAgentClient, agent), cast(Client, temporal))() == (
            age in (60, 89)
        )

    asyncio.run(exercise())
