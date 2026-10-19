"""Worker presence must require both queues without rejecting an ordinary long poll."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from boxie.bootstrap.dependencies import Dependencies
from boxie.sandbox.transport.client import OrchestratorClient
from google.protobuf.timestamp_pb2 import Timestamp
from temporalio.api.taskqueue.v1 import PollerInfo
from temporalio.api.workflowservice.v1 import (
    DescribeNamespaceRequest,
    DescribeNamespaceResponse,
    DescribeTaskQueueRequest,
    DescribeTaskQueueResponse,
)


@pytest.mark.parametrize("age", [None, 89, 91])
@pytest.mark.parametrize("queue", [1, 2])
def test_each_queue_requires_recent_worker_presence(age: int | None, queue: int) -> None:
    async def exercise() -> None:
        class WorkflowService:
            async def describe_namespace(
                self, request: DescribeNamespaceRequest, *, timeout: timedelta
            ) -> DescribeNamespaceResponse:
                assert request.namespace == "boxie" and timeout == timedelta(seconds=5)
                return DescribeNamespaceResponse()

            async def describe_task_queue(
                self, request: DescribeTaskQueueRequest, *, timeout: timedelta
            ) -> DescribeTaskQueueResponse:
                assert request.namespace == "boxie" and timeout == timedelta(seconds=5)
                kind = request.task_queue_type
                selected_age = age if kind == queue else 1
                if selected_age is None:
                    return DescribeTaskQueueResponse()
                timestamp = Timestamp()
                timestamp.FromDatetime(datetime.now(UTC) - timedelta(seconds=selected_age))
                return DescribeTaskQueueResponse(pollers=[PollerInfo(last_access_time=timestamp)])

        class HealthService:
            async def check_health(self) -> bool:
                return True

        class Temporal:
            namespace = "boxie"
            service_client = HealthService()
            workflow_service = WorkflowService()

        def ready(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/readyz"
            assert request.headers["Authorization"] == "Bearer readiness-secret"
            return httpx.Response(
                200,
                json={
                    "identity": {"installation_id": str(uuid4()), "host_id": "host"},
                    "dependencies": {"docker": True},
                },
            )

        async with OrchestratorClient(
            "http://orchestrator.invalid", b"readiness-secret", transport=httpx.MockTransport(ready)
        ) as orchestrator:
            assert await Dependencies(orchestrator, Temporal())() == (age == 89)

    asyncio.run(exercise())
