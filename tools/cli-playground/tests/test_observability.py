"""Playground owns its assembly and entrypoint independently of Boxie."""

import json
import subprocess
import sys
from typing import Any

import pytest


def test_independent_json_producer() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import asyncio, sys
from playground.observability import LogSettings, RequestLogging, bootstrap_logging
from service_telemetry.context import current_context
runtime = bootstrap_logging(LogSettings(service="cli-playground", role="gateway"))
async def downstream(scope, receive, send):
    assert current_context()["request_id"] != "SENTINEL-request-header"
    await send({"type":"http.response.body","body":b"ok"})
async def receive(): return {"type":"http.request"}
async def send(message): assert message["body"] == b"ok"
asyncio.run(RequestLogging(downstream)(
    {"type":"http", "headers":[(b"x-request-id",b"SENTINEL-request-header")]}, receive, send
))
assert not current_context()
runtime.close()
assert not any(name == "boxie" or name.startswith("boxie.") for name in sys.modules)
""",
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    event = json.loads(result.stdout)
    assert event["schema_version"] == 1
    assert event["service"] == "cli-playground"
    assert event["event"] == "request.completed" and "request_id" in event
    assert "SENTINEL" not in result.stdout


def test_entrypoint_catches_configuration_errors() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "playground.bootstrap"],
        capture_output=True,
        text=True,
        timeout=8,
        env={"PATH": "/usr/bin:/bin", "PLAYGROUND_DEPLOY_TOKEN": "SENTINEL_secret"},
    )
    assert result.returncode != 0
    assert result.stderr == ""
    assert "SENTINEL" not in result.stdout
    assert any(json.loads(line)["event"] == "process_failed" for line in result.stdout.splitlines())


def test_process_owner_shares_telemetry_shutdown_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    import os
    import threading
    import time

    import uvicorn
    from playground import app, bootstrap
    from service_telemetry.logging import LogRuntime, LogSettings
    from service_telemetry.metrics import MetricsListener

    read, write = os.pipe()
    os.set_blocking(write, False)
    try:
        while True:
            os.write(write, b"x" * 4096)
    except BlockingIOError:
        pass
    runtime = LogRuntime(
        LogSettings(service="cli-playground", role="gateway"), {}, {}, write, 4096, "enabled"
    )
    runtime.emit("request.completed")  # A real pending writer with a full, unread pipe.
    entered, finished = threading.Event(), threading.Event()
    original_run = MetricsListener._run

    def slow_listener(self: MetricsListener) -> None:
        entered.set()
        try:
            original_run(self)
        finally:
            time.sleep(1.3)  # Deliberately slow owned thread cleanup, after stop signal.
            finished.set()

    async def serve(self: uvicorn.Server, sockets: Any = None) -> None:
        assert await asyncio.to_thread(entered.wait, 1)

    monkeypatch.setattr(MetricsListener, "_run", slow_listener)
    monkeypatch.setattr(bootstrap, "bootstrap_logging", lambda settings: runtime)
    monkeypatch.setattr(app, "create_app", lambda **kwargs: object())
    monkeypatch.setattr(uvicorn.Server, "serve", serve)
    started = time.monotonic()
    try:
        bootstrap.main()
        elapsed = time.monotonic() - started
        assert 1.8 <= elapsed < 2.5, elapsed
        # A repeated terminal close cannot grant either owner another budget.
        again = time.monotonic()
        runtime.close()
        assert time.monotonic() - again < 0.2
        assert finished.wait(0.2)
    finally:
        runtime.close(0)
        os.close(read)
        assert finished.wait(2)
