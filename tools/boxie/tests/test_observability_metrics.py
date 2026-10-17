"""Bounded exposition, cache freshness and private exporter behavior."""

import importlib.util
from dataclasses import replace
from typing import Any

import pytest


def metrics_type() -> tuple[Any, Any]:
    assert importlib.util.find_spec("boxie.observability.metrics") is not None, (
        "authoritative metrics missing"
    )
    from boxie.observability.metrics import Metrics, OperationalSnapshot

    return Metrics, OperationalSnapshot


def test_authoritative_snapshot_is_atomic_stale_and_restartable() -> None:
    Metrics, Snapshot = metrics_type()
    now = [10.0]
    metrics = Metrics("api", clock=lambda: now[0])
    assert b"boxie_capacity_reserved " not in metrics.render()
    sample = Snapshot(1000, (1, 0, 0, 1, 0, 0), 2, 8, 1, 120.0, 0, ((2, 5.0), (1, 2.0)))
    metrics.refresh(sample)
    body = metrics.render()
    assert b"boxie_capacity_reserved 2.0" in body
    assert b"boxie_cleanup_oldest_age_seconds 120.0" in body
    metrics.refresh_failed()
    now[0] = 20
    assert b"boxie_cleanup_oldest_age_seconds 130.0" in metrics.render()
    now[0] = 41
    assert b"boxie_capacity_reserved " not in metrics.render()
    assert b"boxie_snapshot_fresh 0.0" in metrics.render()
    restarted = Metrics("api")
    restarted.refresh(sample)
    assert b"boxie_capacity_reserved 2.0" in restarted.render()
    assert b'boxie_provisioning_total{outcome="ready"} 0.0' in restarted.render()
    restarted.refresh(replace(sample, cleanup_unknown=1))
    assert b"boxie_cleanup_oldest_age_seconds " not in restarted.render()
    assert b"boxie_capacity_reserved " not in Metrics("orchestrator").render()


def test_bounded_labels_and_lease_unknown_empty_expired() -> None:
    Metrics, _ = metrics_type()
    now = [10.0]
    metrics = Metrics("orchestrator", clock=lambda: now[0])
    metrics.refresh_lease(0, None)
    assert b"boxie_orchestrator_control_lease_active 0.0" in metrics.render()
    assert b"boxie_orchestrator_control_lease_remaining_seconds " not in metrics.render()
    metrics.refresh_lease(2, 3)
    now[0] = 14
    assert b"boxie_orchestrator_control_lease_remaining_seconds 0.0" in metrics.render()
    now[0] = 16
    assert b"boxie_orchestrator_control_lease_active " not in metrics.render()
    for identifier in ("sandbox-secret", "request-secret", "/v1/sandboxes/secret"):
        metrics.http_completed(identifier, identifier, "200", 0.1)
    body = metrics.render()
    assert b"secret" not in body
    assert b'method="other"' in body
    assert b'route="unresolved"' in body
    metrics.dependency("docker", True)
    assert b'boxie_dependency_ready{dependency="docker"} 1.0' in metrics.render()
    now[0] = 47
    assert b'boxie_dependency_ready{dependency="docker"}' not in metrics.render()


def test_http_completion_uses_bounded_route_template() -> None:
    import asyncio

    from boxie.observability.telemetry.asgi import RequestLogging

    Metrics, _ = metrics_type()
    metrics = Metrics("api")

    async def app(scope: Any, receive: Any, send: Any) -> None:
        scope["route"] = type("Route", (), {"path": "/v1/sandboxes/{sandbox_id}"})()
        await send({"type": "http.response.start", "status": 201})
        await send({"type": "http.response.body", "body": b""})

    async def receive() -> dict[str, str]:
        return {"type": "http.disconnect"}

    async def send(message: Any) -> None:
        pass

    async def exercise() -> None:
        await RequestLogging(app, completed=metrics.http_completed)(
            {"type": "http", "method": "POST", "path": "/secret"}, receive, send
        )

    asyncio.run(exercise())
    assert (
        b'boxie_http_requests_total{method="POST",route="/v1/sandboxes/{sandbox_id}",status_class="2xx"} 1.0'
        in metrics.render()
    )


def test_private_listener_collision_recovers_and_closes() -> None:
    import socket
    import time
    import urllib.request

    from boxie.observability.telemetry.metrics import MetricsListener

    Metrics, _ = metrics_type()
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        listener = MetricsListener(Metrics("orchestrator"), "127.0.0.1", port, retry_seconds=0.02)
        listener.start()
        assert listener.address is None
    try:
        deadline = time.monotonic() + 2
        while listener.address is None and time.monotonic() < deadline:
            time.sleep(0.01)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=1) as response:
            assert response.status == 200
            assert b"boxie_provisioning_total" in response.read()
    finally:
        listener.close()
    replacement = MetricsListener(Metrics("orchestrator"), "127.0.0.1", port)
    replacement.start()
    try:
        deadline = time.monotonic() + 2
        while replacement.address is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert replacement.address is not None
    finally:
        replacement.close()


def test_sdk_exporter_bind_failure_keeps_safe_default_runtime() -> None:
    import subprocess
    import sys

    code = """
import socket
from boxie.observability.logging import process_logging
from temporalio.runtime import Runtime
with socket.socket() as occupied:
    occupied.bind(('127.0.0.1',9201))
    occupied.listen()
    logs = process_logging('api')
    assert Runtime.default() is not None
    assert logs.stats.configuration_errors == 1
    assert process_logging('api') is logs
    logs.close()
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr


def test_disabled_log_sink_is_visible_without_traffic() -> None:
    from boxie.observability.telemetry.logging import LogRuntime, LogSettings
    from boxie.observability.telemetry.metrics import ProcessMetrics

    runtime = LogRuntime(LogSettings(service="boxie", role="api"), {}, {}, None, 4096, "disabled")
    try:
        body = ProcessMetrics("boxie", logs=runtime).render()
        assert b"boxie_log_sink_available 0.0" in body
    finally:
        runtime.close()


@pytest.mark.parametrize("cancel", [False, True])
def test_disconnect_completion_is_cancelled_once(
    cancel: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import json
    import os

    from boxie.observability.metrics import Metrics
    from boxie.observability.telemetry.asgi import RequestLogging
    from boxie.observability.telemetry.logging import LogRuntime, LogSettings

    read, write = os.pipe()
    runtime = LogRuntime(LogSettings(service="boxie", role="api"), {}, {}, write, 4096, "enabled")
    metrics = Metrics("api")
    monkeypatch.setattr("boxie.observability.telemetry.asgi.configured_runtime", lambda: runtime)

    async def receive() -> dict[str, str]:
        return {"type": "http.disconnect"}

    async def send(message: Any) -> None:
        pass

    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200})
        assert (await receive())["type"] == "http.disconnect"
        if cancel:
            raise asyncio.CancelledError()
        # Return without completing a response body after the observed disconnect.

    async def exercise() -> None:
        completion = RequestLogging(app, completed=metrics.http_completed)(
            {"type": "http", "method": "GET"}, receive, send
        )
        if cancel:
            with pytest.raises(asyncio.CancelledError):
                await completion
        else:
            await completion

    try:
        asyncio.run(exercise())
        runtime.close()
        records = [json.loads(line) for line in os.read(read, 65536).splitlines()]
        assert len(records) == 1
        assert records[0]["event"] == "request.completed"
        assert records[0]["outcome"] == "cancelled"
        assert (
            b'boxie_http_requests_total{method="GET",route="unresolved",status_class="disconnected"} 1.0'
            in metrics.render()
        )
    finally:
        runtime.close()
        os.close(read)


def test_private_listener_expires_stalled_clients_without_stderr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    import socket
    import time
    import urllib.request

    from boxie.observability.metrics import Metrics
    from boxie.observability.telemetry.metrics import MetricsListener

    listener = MetricsListener(Metrics("orchestrator"), "127.0.0.1", 0)
    listener.start()
    clients: list[socket.socket] = []
    try:
        deadline = time.monotonic() + 2
        while listener.address is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert listener.address is not None
        for _ in range(8):
            client = socket.create_connection(listener.address, timeout=1)
            client.sendall(b"GET /metrics HTTP/1.1\r\nX-Private: never-log-this")
            clients.append(client)
        time.sleep(1.2)
        # A finite header deadline returns capacity, including under slow-client load.
        with urllib.request.urlopen(
            f"http://{listener.address[0]}:{listener.address[1]}/metrics", timeout=2
        ) as response:
            assert response.status == 200
        for client in clients:
            try:
                assert client.recv(1) == b""
            except ConnectionResetError:
                pass  # Excess concurrent clients may be rejected without a response.
        assert not capsys.readouterr().err
    finally:
        for client in clients:
            client.close()
        listener.close()


def test_dependency_probes_follow_configured_namespace_and_owned_queues() -> None:
    import asyncio
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from boxie.domain.models import OrchestratorIdentity
    from boxie.observability.metrics import Metrics
    from boxie.observability.runtime import api_dependencies
    from boxie.temporal.contracts import lifecycle_queue, maintenance_queue
    from google.protobuf.timestamp_pb2 import Timestamp
    from temporalio.api.taskqueue.v1 import PollerInfo
    from temporalio.api.workflowservice.v1 import (
        DescribeNamespaceRequest,
        DescribeNamespaceResponse,
        DescribeTaskQueueRequest,
        DescribeTaskQueueResponse,
    )

    async def exercise() -> None:
        identity = OrchestratorIdentity(uuid4(), "configured host")
        seen: list[tuple[str, str, int]] = []
        observed = asyncio.Event()

        async def ready() -> bool:
            return True

        async def namespace(
            request: DescribeNamespaceRequest, **kwargs: Any
        ) -> DescribeNamespaceResponse:
            assert request.namespace == "configured-namespace"
            return DescribeNamespaceResponse()

        async def queue(
            request: DescribeTaskQueueRequest, **kwargs: Any
        ) -> DescribeTaskQueueResponse:
            seen.append((request.namespace, request.task_queue.name, request.task_queue_type))
            if len(seen) == 4:
                observed.set()
            timestamp = Timestamp()
            timestamp.FromDatetime(datetime.now(UTC))
            return DescribeTaskQueueResponse(pollers=[PollerInfo(last_access_time=timestamp)])

        temporal: Any = SimpleNamespace(
            namespace="configured-namespace",
            service_client=SimpleNamespace(check_health=ready),
            workflow_service=SimpleNamespace(
                describe_namespace=namespace, describe_task_queue=queue
            ),
        )
        orchestrator: Any = SimpleNamespace(ready=ready)
        metrics = Metrics("api")
        task = asyncio.create_task(api_dependencies(metrics, orchestrator, temporal, identity))
        try:
            await asyncio.wait_for(observed.wait(), 1)
            await asyncio.sleep(0)
            assert {(namespace, queue) for namespace, queue, _ in seen} == {
                ("configured-namespace", lifecycle_queue(identity)),
                ("configured-namespace", maintenance_queue(identity)),
            }
            assert all(
                f'boxie_dependency_ready{{dependency="{name}"}} 1.0'.encode() in metrics.render()
                for name in (
                    "workflow_poller",
                    "activity_poller",
                    "maintenance_workflow_poller",
                    "maintenance_activity_poller",
                )
            )
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(exercise())


def test_invalid_metrics_bind_does_not_prevent_application_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from boxie.observability.runtime import run_metrics_process
    from boxie.observability.telemetry.logging import LogRuntime, LogSettings

    runtime = LogRuntime(LogSettings(service="boxie", role="api"), {}, {}, None, 4096, "disabled")
    monkeypatch.setenv("BOXIE_METRICS_HOST", "never-log-this.invalid")
    started: list[str] = []
    run_metrics_process(runtime, "api", lambda metrics: started.append(metrics.role))
    assert started == ["api"]
    assert runtime.stats.configuration_errors == 1
