"""Explicit namespace operation using the same pinned SDK as the Boxie worker."""

import argparse
import asyncio
from collections.abc import Awaitable
from datetime import timedelta
from pathlib import Path
from typing import Protocol

from google.protobuf.duration_pb2 import Duration
from temporalio.api.enums.v1 import NamespaceState
from temporalio.api.workflowservice.v1 import (
    DescribeNamespaceRequest,
    DescribeNamespaceResponse,
    RegisterNamespaceRequest,
    RegisterNamespaceResponse,
)
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode, TLSConfig


class NamespaceService(Protocol):
    def describe_namespace(
        self, req: DescribeNamespaceRequest, /, *, timeout: timedelta
    ) -> Awaitable[DescribeNamespaceResponse]: ...
    def register_namespace(
        self, req: RegisterNamespaceRequest, /, *, timeout: timedelta
    ) -> Awaitable[RegisterNamespaceResponse]: ...


async def ensure_namespace(service: NamespaceService, namespace: str = "boxie") -> None:
    """Create only on typed NotFound; every success verifies live configuration."""
    request = DescribeNamespaceRequest(namespace=namespace)
    retention = Duration(seconds=604800)
    timeout = timedelta(seconds=10)
    try:
        description = await service.describe_namespace(request, timeout=timeout)
    except RPCError as error:
        if error.status != RPCStatusCode.NOT_FOUND:
            raise
        try:
            await service.register_namespace(
                RegisterNamespaceRequest(
                    namespace=namespace, workflow_execution_retention_period=retention
                ),
                timeout=timeout,
            )
        except RPCError as registration_error:
            if registration_error.status != RPCStatusCode.ALREADY_EXISTS:
                raise
        description = await service.describe_namespace(request, timeout=timeout)
    if (
        description.namespace_info.name != namespace
        or description.namespace_info.state != NamespaceState.NAMESPACE_STATE_REGISTERED
        or description.config.workflow_execution_retention_ttl != retention
    ):
        raise ValueError("namespace must be registered with seven-day retention")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", default="temporal:7233")
    parser.add_argument("--namespace", default="boxie")
    parser.add_argument("--server-name", default="temporal")
    parser.add_argument("--ca-file", type=Path, default=Path("/certs/frontend-ca.pem"))
    parser.add_argument("--cert-file", type=Path, default=Path("/certs/operator.pem"))
    parser.add_argument("--key-file", type=Path, default=Path("/certs/operator.key"))
    args = parser.parse_args()

    async def run() -> None:
        async with asyncio.timeout(10):
            client = await Client.connect(
                args.address,
                namespace=args.namespace,
                tls=TLSConfig(
                    server_root_ca_cert=args.ca_file.read_bytes(),
                    domain=args.server_name,
                    client_cert=args.cert_file.read_bytes(),
                    client_private_key=args.key_file.read_bytes(),
                ),
            )
        await ensure_namespace(client.workflow_service, args.namespace)
        # Namespace registration propagates asynchronously to independent server
        # caches. The operator grace also covers an interrupted prior bootstrap.
        await asyncio.sleep(20)

    asyncio.run(run())


if __name__ == "__main__":
    main()
