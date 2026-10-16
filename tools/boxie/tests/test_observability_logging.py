"""Real-process logging contract; stdout is always an exclusively owned pipe."""

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from integration.conftest import postgres_dsn as postgres_dsn
from integration.conftest import postgres_server as postgres_server
from integration.conftest import temporal_stack as temporal_stack

PRELUDE = """
import asyncio, json, logging, os, sys, threading, time
from pathlib import Path
from dataclasses import asdict
from uuid import UUID
from boxie.observability.logging import LogSettings, configure_logging, bootstrap_logging
from boxie.observability.context import bind_context
runtime = bootstrap_logging(LogSettings(service="boxie", role="api"))
"""
SECRET = "SENTINEL_http_auth_terminal_history_secret"


def child(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", PRELUDE + code], capture_output=True, text=True, timeout=5
    )


def records(result: subprocess.CompletedProcess[str]) -> list[dict[str, object]]:
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    lines = result.stdout.splitlines()
    assert lines
    events = [json.loads(line) for line in lines]
    schema = json.loads(
        (Path(__file__).parents[3] / "infra/observability/event.schema.json").read_text()
    )
    for event in events:
        assert set(schema["required"]) <= event.keys() <= schema["properties"].keys()
        assert event["schema_version"] == 1
        for key, value in event.items():
            rules = schema["properties"][key]
            if "enum" in rules:
                assert value in rules["enum"]
            if "maxLength" in rules:
                assert len(value) <= rules["maxLength"]
    assert (
        all(len(line.encode()) + 1 <= 512 for line in lines) if sys.platform == "darwin" else True
    )
    return events


def test_json_context_isolation_and_terminal_close() -> None:
    result = child("""
assert configure_logging(runtime.settings) is runtime
assert configure_logging(LogSettings(service="boxie", role="worker")) is runtime
async def task(number):
    with bind_context(request_id=str(UUID(int=number))):
        await asyncio.sleep(0)
        runtime.emit("request_completed", duration_ms=number)
async def run(): await asyncio.gather(task(1), task(2))
asyncio.run(run())
runtime.emit("request_completed")
assert runtime.stats.configuration_errors == 1
runtime.close()
assert configure_logging(runtime.settings) is runtime
assert runtime.stats.closed
""")
    events = records(result)
    assert [event.get("request_id") for event in events] == [
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
        None,
    ]


def test_foreign_messages_exception_chains_and_raw_bytes_are_private() -> None:
    result = child(f"""
secret = {SECRET!r}
class Hostile:
    def __str__(self): raise AssertionError("must never format")
    def __repr__(self): raise AssertionError("must never repr")
logging.getLogger("httpx").error("GET https://example/?token=%s", secret)
logging.getLogger(secret).error(Hostile(), Hostile())
try:
    try: raise ValueError(secret + "\\nembedded newline")
    except ValueError as e:
        e.add_note(secret)
        raise RuntimeError(secret) from e
except RuntimeError:
    logging.getLogger("uvicorn.error").exception(secret)
    runtime.emit("request_failed", error=sys.exception(), request_id=secret, token=secret)
os.write(1, secret.encode()); os.write(2, secret.encode())
print(secret, end="")
runtime.close()
""")
    events = records(result)
    assert SECRET not in result.stdout + result.stderr
    assert any(event.get("error_type") == "RuntimeError" for event in events)
    assert any(event.get("error_message") == "operation failed" for event in events)


@pytest.mark.parametrize("explicit", [False, True])
def test_unread_pipe_exit_overflow_and_bounded_async_progress(
    tmp_path: Path, explicit: bool
) -> None:
    evidence = tmp_path / "evidence.json"
    code = (
        PRELUDE
        + f"""
async def run():
    started = time.monotonic()
    for _ in range(20000):
        runtime.emit("request_completed")
    # HTTP/PTY work must not wait for the collector.
    await asyncio.sleep(0)
    assert time.monotonic() - started < 1.5
    assert runtime.stats.dropped_count > 0
    assert runtime.stats.queued_records <= 2048
asyncio.run(run())
Path({str(evidence)!r}).write_text(json.dumps(asdict(runtime.stats)))
if {explicit!r}: runtime.close()
"""
    )
    started = time.monotonic()
    process = subprocess.Popen(
        [sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        assert process.wait(timeout=3) == 0
        assert time.monotonic() - started < 3
        assert json.loads(evidence.read_text())["dropped_count"] > 0
        out, err = process.communicate(timeout=1)
        assert not err
        assert out
        assert all(json.loads(line)["schema_version"] == 1 for line in out.splitlines())
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=1)


def test_factory_import_does_not_take_over_streams() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import boxie.bootstrap.api; print('intact')"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0
    assert result.stdout == "intact\n"


@pytest.mark.parametrize("role", ["api", "agent", "worker"])
def test_real_entrypoint_startup_failure_is_sanitized(role: str, tmp_path: Path) -> None:
    path = tmp_path / SECRET
    path.write_text('{"password":"' + SECRET + '"}')
    result = subprocess.run(
        [sys.executable, "-m", "boxie.bootstrap." + role, "--config", str(path)],
        capture_output=True,
        text=True,
        timeout=8,
    )
    assert result.returncode != 0
    assert not result.stderr
    assert SECRET not in result.stdout
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert any(event["event"] == "process_failed" for event in events)


def test_unsupported_sink_is_nonfatal(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.json"
    with (tmp_path / "sink").open("wb") as sink:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                PRELUDE
                + f"""
runtime.emit("request_completed")
runtime.close()
Path({str(evidence)!r}).write_text(json.dumps(asdict(runtime.stats)))
""",
            ],
            stdout=sink,
            stderr=subprocess.PIPE,
            timeout=5,
        )
    assert result.returncode == 0
    stats = json.loads(evidence.read_text())
    assert stats["sink_state"] == "unsupported"
    assert stats["dropped_count"] > 0
    assert (tmp_path / "sink").read_bytes() == b""


def test_atomic_truncation_preserves_json_and_omits_ids_intact() -> None:
    code = """
from service_logging.logging import bootstrap_logging, LogSettings
from service_logging.context import bind_context
runtime = bootstrap_logging(LogSettings(service="boxie", role="api"), events={"multiline": "é\\n" * 64})
with bind_context(request_id="11111111-1111-1111-1111-111111111111", workflow_id="boxie/sandbox/11111111-1111-1111-1111-111111111111"):
    runtime.emit("multiline")
runtime.close()
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=5)
    events = records(result)
    assert isinstance(events[0]["message"], str)
    assert "\n" in events[0]["message"]
    if sys.platform == "darwin":
        assert events[0]["truncated"] is True
        assert "workflow_id" not in events[0]
    assert events[0].get("request_id") in (None, "11111111-1111-1111-1111-111111111111")


def test_thread_asyncio_unraisable_hooks_and_structlog() -> None:
    result = child(f"""
import structlog, gc
from service_logging.logging import install_asyncio_handler
secret = {SECRET!r}
def fail(): raise ValueError(secret)
thread = threading.Thread(target=fail); thread.start(); thread.join()
class Unraisable:
    def __del__(self): raise KeyError(secret)
Unraisable(); gc.collect()
async def run():
    install_asyncio_handler(runtime)
    asyncio.get_running_loop().call_exception_handler({{"message":secret,"exception":RuntimeError(secret)}})
asyncio.run(run())
structlog.get_logger().info("request_completed", request_id=secret, message=secret, level=secret)
runtime.close()
""")
    events = records(result)
    assert {event["event"] for event in events} == {
        "thread_failed",
        "unraisable_failed",
        "async_failed",
        "request_completed",
    }
    assert SECRET not in result.stdout + result.stderr


def test_broken_reader_disables_delivery(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.json"
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            PRELUDE
            + f"""
for _ in range(100):
    runtime.emit("request_completed")
    if runtime.stats.sink_state == "broken": break
    time.sleep(.01)
runtime.close()
Path({str(evidence)!r}).write_text(json.dumps(asdict(runtime.stats)))
""",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None
    process.stdout.close()
    try:
        assert process.wait(timeout=3) == 0
        stats = json.loads(evidence.read_text())
        assert stats["sink_state"] == "broken"
        assert stats["dropped_count"] > 0
        assert process.stderr is not None and not process.stderr.read()
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=1)
        if process.stderr is not None:
            process.stderr.close()


def test_real_http_and_pty_progress_while_log_pipe_is_unread(tmp_path: Path) -> None:
    evidence = tmp_path / "progress.json"
    # Real API HTTP and OS PTY; application ports only are minimal injected doubles.
    # Full Temporal + agent/WebSocket path is exercised separately by the live fixture.
    code = (
        PRELUDE
        + f'''
import httpx, pty, tty
from boxie.api.app import ApiConfig, create_app
from boxie_test_support.api import serve
class Ports:
    async def ready(self): return True
ports = Ports()
app = create_app(ApiConfig("test", {{"one":b"consumer-one-secret"}}), ports, ports, ports)
async def run():
    async with serve(app) as origin:
        for _ in range(12000): runtime.emit("request_completed")
        assert runtime.stats.dropped_count > 0
        async with httpx.AsyncClient(base_url=origin) as http:
            response = await asyncio.wait_for(http.get("/healthz?secret={SECRET}", headers={{"X-Request-ID":"{SECRET}"}}), 1)
            assert response.status_code == 200
        master, slave = pty.openpty()
        try:
            tty.setraw(slave)
            os.set_blocking(slave, False)
            os.write(master, b"{SECRET}")
            for _ in range(100):
                try:
                    assert os.read(slave, 4096) == b"{SECRET}"
                    break
                except BlockingIOError: await asyncio.sleep(.001)
            else: raise AssertionError("PTY stalled")
        finally:
            os.close(master); os.close(slave)
    Path({str(evidence)!r}).write_text(json.dumps({{"terminal_roundtrip_completed":True,"drops":runtime.stats.dropped_count}}))
asyncio.run(run())
runtime.close(.1)
'''
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        status = process.wait(timeout=5)
        if status:
            assert process.stdout is not None
            raise AssertionError(process.stdout.read().decode())
        result = json.loads(evidence.read_text())
        assert result["terminal_roundtrip_completed"] and result["drops"] > 0
        output, errors = process.communicate(timeout=1)
        assert not errors
        assert SECRET.encode() not in output
        assert all(json.loads(line)["schema_version"] == 1 for line in output.splitlines())
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=1)


@pytest.mark.integration
def test_real_temporal_history_and_http_terminal_logs_are_private(
    postgres_dsn: str,
    temporal_stack: object,
    tmp_path: Path,
) -> None:
    """Run the existing real boundary scenario inside an early-bootstrapped process.

    Parent fixtures own disposable SQL/Temporal resources. The scenario verifies
    real HTTP/WS and worker replacement, scans decoded history wire payloads and
    replays it. Only the agent's Docker port is substituted by that fixture.
    """
    from typing import Any, cast

    fixture = cast(Any, temporal_stack)
    config = tmp_path / "fixture.json"
    config.write_text(
        json.dumps({"dsn": postgres_dsn, "address": fixture.address, "root": str(fixture.root)})
    )
    config.chmod(0o600)
    scenario_root = tmp_path / "scenario"
    scenario_root.mkdir()
    tests = Path(__file__).parent
    code = f"""
import sys, json
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, {str(tests)!r})
from boxie.observability.logging import process_logging
runtime = process_logging("worker")
import pytest
from test_outbox import test_runnable_stack_preserves_accounting_and_worker_replacement
settings = json.loads(Path({str(config)!r}).read_text())
fixture = SimpleNamespace(address=settings["address"], root=Path(settings["root"]))
# Existing caplog check is supplemented by the parent's actual output check.
with pytest.MonkeyPatch.context() as patch:
    test_runnable_stack_preserves_accounting_and_worker_replacement(
        settings["dsn"], fixture, Path({str(scenario_root)!r}), False, patch, SimpleNamespace(text="")
    )
runtime.emit("request_completed", outcome="succeeded")
runtime.close()
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=150
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert events and all(event["schema_version"] == 1 for event in events)
    exported_history = (scenario_root / "production-history.json").read_text()
    logs = result.stdout + result.stderr
    for sentinel in ("SECRET-", "consumer-one-secret", "agent-test-secret"):
        assert sentinel not in logs + exported_history
    assert any(
        event["event"] == "request_completed" and event.get("outcome") == "succeeded"
        for event in events
    )
