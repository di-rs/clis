"""Pinned server signal semantics with an actually unread production log pipe."""

import json
import signal
import subprocess
import sys
import time
from pathlib import Path


def test_uvicorn_graceful_sigterm_finishes_lifespan_with_unread_stdout(tmp_path: Path) -> None:
    ready, stopped = tmp_path / "ready.json", tmp_path / "stopped"
    program = f"""
import asyncio, json
from contextlib import asynccontextmanager
from pathlib import Path
import uvicorn
from fastapi import FastAPI
from boxie.observability.logging import LogSettings, bootstrap_logging
from boxie.observability.telemetry.logging import run_process
runtime = bootstrap_logging(LogSettings(service='boxie', role='api'))
@asynccontextmanager
async def lifespan(app):
    for _ in range(20000):
        runtime.emit('request.completed')
    Path({str(ready)!r}).write_text(json.dumps({{'drops': runtime.stats.dropped_count, 'queued': runtime.stats.queued_records}}))
    yield
    Path({str(stopped)!r}).write_text('lifespan completed')
app = FastAPI(lifespan=lifespan)
server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=0, access_log=False, log_config=None))
run_process(runtime, lambda: asyncio.run(server.serve()))
"""
    process = subprocess.Popen(
        [sys.executable, "-c", program], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    assert process.stdout is not None
    try:
        deadline = time.monotonic() + 5
        while not ready.exists():
            assert process.poll() is None and time.monotonic() < deadline
            time.sleep(0.01)
        stats = json.loads(ready.read_text())
        assert stats["drops"] > 0 and stats["queued"] > 0
        started = time.monotonic()
        process.terminate()
        assert process.wait(timeout=8) == -signal.SIGTERM
        assert time.monotonic() - started < 8
        assert stopped.read_text() == "lifespan completed"
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=1)
        process.stdout.close()  # Remains unread through completed lifespan/process exit.


def test_shutdown_requires_same_unread_child_and_exact_finite_exit_semantics() -> None:
    import pytest
    from observability_infra.smoke_pipe_api import validate_shutdown

    active = {"pid": 123, "start": "456", "unread": True}
    result = {"pid": 123, "start": "456", "exit": -signal.SIGTERM, "forced": False, "seconds": 0.2}
    validate_shutdown(active, result)
    validate_shutdown(active, {**result, "exit": 0})
    for changed in (
        {"pid": 124},
        {"start": "457"},
        {"exit": 1},
        {"exit": -signal.SIGKILL},
        {"forced": True},
        {"seconds": 8.01},
        {"seconds": float("nan")},
        {"exit": False},
    ):
        with pytest.raises(RuntimeError):
            validate_shutdown(active, {**result, **changed})
    with pytest.raises(RuntimeError):
        validate_shutdown({**active, "unread": False}, result)
