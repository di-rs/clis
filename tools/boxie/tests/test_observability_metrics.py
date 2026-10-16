"""Bounded exposition, cache freshness and private exporter behavior."""

import importlib.util
from dataclasses import replace
from typing import Any


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
    sample = Snapshot(1000, (1, 0, 0, 1, 0, 0), 2, 8, 1, 120.0, 0, ((2, 5.0), (1, 2.0), (0, 0.0)))
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
    assert b"boxie_capacity_reserved " not in Metrics("worker").render()


def test_bounded_labels_and_lease_unknown_empty_expired() -> None:
    Metrics, _ = metrics_type()
    now = [10.0]
    metrics = Metrics("agent", clock=lambda: now[0])
    metrics.refresh_lease(0, None)
    assert b"boxie_agent_control_lease_active 0.0" in metrics.render()
    assert b"boxie_agent_control_lease_remaining_seconds " not in metrics.render()
    metrics.refresh_lease(2, 3)
    now[0] = 14
    assert b"boxie_agent_control_lease_remaining_seconds 0.0" in metrics.render()
    now[0] = 16
    assert b"boxie_agent_control_lease_active " not in metrics.render()
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


def test_http_completion_is_bounded_and_disconnect_is_distinct() -> None:
    import asyncio

    from service_logging.asgi import RequestLogging

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

    from service_logging.metrics import MetricsListener

    Metrics, _ = metrics_type()
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        listener = MetricsListener(Metrics("worker"), "127.0.0.1", port, retry_seconds=0.02)
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
    replacement = MetricsListener(Metrics("worker"), "127.0.0.1", port)
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
    from service_logging.logging import LogRuntime, LogSettings
    from service_logging.metrics import ProcessMetrics

    runtime = LogRuntime(LogSettings(service="boxie", role="api"), {}, {}, None, 4096, "disabled")
    try:
        body = ProcessMetrics("boxie", logs=runtime).render()
        assert b"boxie_log_sink_available 0.0" in body
    finally:
        runtime.close()
