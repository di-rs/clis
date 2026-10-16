"""Namespace bootstrap classifies typed service failures and verifies live state."""

import asyncio
from datetime import timedelta
from typing import Any, cast

import pytest
from google.protobuf.duration_pb2 import Duration
from temporalio.api.enums.v1 import NamespaceState
from temporalio.api.namespace.v1 import NamespaceConfig, NamespaceInfo
from temporalio.api.workflowservice.v1 import (
    DescribeNamespaceRequest,
    DescribeNamespaceResponse,
    RegisterNamespaceRequest,
    RegisterNamespaceResponse,
)
from temporalio.service import RPCError, RPCStatusCode, WorkflowService


class NamespacePeer:
    """The remote RPC boundary; errors use real SDK status codes and messages."""

    def __init__(
        self,
        *,
        missing: bool = False,
        describe_error: RPCStatusCode | None = None,
        register_error: RPCStatusCode | None = None,
        retention: int = 604800,
        state: NamespaceState.ValueType = NamespaceState.NAMESPACE_STATE_REGISTERED,
        name: str = "boxie",
    ) -> None:
        self.missing = missing
        self.describe_error = describe_error
        self.register_error = register_error
        self.retention, self.state, self.name = retention, state, name
        self.calls: list[str] = []

    async def describe_namespace(
        self, request: DescribeNamespaceRequest, **options: Any
    ) -> DescribeNamespaceResponse:
        assert request.namespace == "boxie"
        assert options["timeout"] == timedelta(seconds=10)
        self.calls.append("describe")
        if self.describe_error is not None:
            raise RPCError("Namespace boxie already exists", self.describe_error, b"")
        if self.missing:
            self.missing = False
            raise RPCError("missing namespace", RPCStatusCode.NOT_FOUND, b"")
        return DescribeNamespaceResponse(
            namespace_info=NamespaceInfo(name=self.name, state=self.state),
            config=NamespaceConfig(
                workflow_execution_retention_ttl=Duration(seconds=self.retention)
            ),
        )

    async def register_namespace(
        self, request: RegisterNamespaceRequest, **options: Any
    ) -> RegisterNamespaceResponse:
        assert request.namespace == "boxie"
        assert request.workflow_execution_retention_period == Duration(seconds=604800)
        assert options["timeout"] == timedelta(seconds=10)
        self.calls.append("register")
        if self.register_error is not None:
            # Deliberately misleading text: only the status may classify the failure.
            raise RPCError("Namespace boxie already exists", self.register_error, b"")
        return RegisterNamespaceResponse()


@pytest.mark.parametrize("race", [False, True])
def test_namespace_bootstrap_registers_absent_namespace_and_verifies_race(race: bool) -> None:
    from boxie.bootstrap.temporal import ensure_namespace

    peer = NamespacePeer(
        missing=True, register_error=RPCStatusCode.ALREADY_EXISTS if race else None
    )
    asyncio.run(ensure_namespace(cast(WorkflowService, peer)))
    assert peer.calls == ["describe", "register", "describe"]


def test_namespace_bootstrap_verifies_existing_namespace_without_registration() -> None:
    from boxie.bootstrap.temporal import ensure_namespace

    peer = NamespacePeer()
    asyncio.run(ensure_namespace(cast(WorkflowService, peer)))
    assert peer.calls == ["describe"]


@pytest.mark.parametrize("stage", ["describe", "register"])
@pytest.mark.parametrize(
    "status",
    [RPCStatusCode.UNAVAILABLE, RPCStatusCode.UNAUTHENTICATED, RPCStatusCode.PERMISSION_DENIED],
)
def test_namespace_bootstrap_propagates_transport_and_authentication_errors(
    stage: str, status: RPCStatusCode
) -> None:
    from boxie.bootstrap.temporal import ensure_namespace

    peer = NamespacePeer(
        missing=stage == "register",
        describe_error=status if stage == "describe" else None,
        register_error=status if stage == "register" else None,
    )
    with pytest.raises(RPCError) as raised:
        asyncio.run(ensure_namespace(cast(WorkflowService, peer)))
    assert raised.value.status == status
    assert peer.calls == (["describe"] if stage == "describe" else ["describe", "register"])


@pytest.mark.parametrize("missing", [False, True])
@pytest.mark.parametrize(
    "mismatch",
    [{"retention": 3600}, {"state": NamespaceState.NAMESPACE_STATE_DEPRECATED}, {"name": "other"}],
)
def test_namespace_bootstrap_rejects_wrong_namespace_state_or_retention(
    missing: bool, mismatch: dict[str, Any]
) -> None:
    from boxie.bootstrap.temporal import ensure_namespace

    peer = NamespacePeer(missing=missing, **mismatch)
    with pytest.raises(ValueError, match="registered.*seven-day"):
        asyncio.run(ensure_namespace(cast(WorkflowService, peer)))
