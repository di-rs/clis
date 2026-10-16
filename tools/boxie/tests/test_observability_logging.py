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
        runtime.emit("request.completed", duration_ms=number)
async def run(): await asyncio.gather(task(1), task(2))
asyncio.run(run())
runtime.emit("request.completed")
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
    runtime.emit("request.completed", error=sys.exception(), request_id=secret, token=secret)
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
        runtime.emit("request.completed")
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
runtime.emit("request.completed")
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
from service_telemetry.logging import bootstrap_logging, LogSettings
from service_telemetry.context import bind_context
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
from service_telemetry.logging import install_asyncio_handler
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
structlog.get_logger().info("request.completed", request_id=secret, message=secret, level=secret)
runtime.close()
""")
    events = records(result)
    assert {event["event"] for event in events} == {
        "thread_failed",
        "unraisable_failed",
        "async_failed",
        "request.completed",
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
    runtime.emit("request.completed")
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
        for _ in range(12000): runtime.emit("request.completed")
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
runtime.emit("request.completed", outcome="succeeded")
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
        event["event"] == "request.completed" and event.get("outcome") == "succeeded"
        for event in events
    )


@pytest.mark.parametrize("closed_fds", [(2,), (0, 2), (0,)])
def test_pipe_sink_avoids_all_standard_descriptors(
    tmp_path: Path,
    closed_fds: tuple[int, ...],
) -> None:
    evidence = tmp_path / "descriptors.json"
    code = f"""
import json, os, stat
from dataclasses import asdict
from pathlib import Path
from boxie.observability.logging import LogSettings, bootstrap_logging
original = os.fstat(1)
for fd in {closed_fds!r}: os.close(fd)
runtime = bootstrap_logging(LogSettings(service="boxie", role="api"))
sink = runtime._sink
assert sink is not None
actual = os.fstat(sink)
identity = {{
    "fd": sink,
    "same_pipe": (actual.st_dev, actual.st_ino) == (original.st_dev, original.st_ino),
    "fifo": stat.S_ISFIFO(actual.st_mode),
    "inheritable": os.get_inheritable(sink),
    "blocking": os.get_blocking(sink),
}}
runtime.emit("request.completed")
runtime.close()
redirects = {{}}
for fd in (1, 2):
    try: redirects[str(fd)] = stat.S_ISCHR(os.fstat(fd).st_mode)
    except OSError: redirects[str(fd)] = False
Path({str(evidence)!r}).write_text(json.dumps({{"sink":identity,"stats":asdict(runtime.stats),"redirects":redirects}}))
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    measured = json.loads(evidence.read_text())
    assert measured["sink"]["fd"] >= 3, measured
    assert measured["sink"]["same_pipe"] and measured["sink"]["fifo"], measured
    assert not measured["sink"]["inheritable"] and not measured["sink"]["blocking"], measured
    assert measured["redirects"] == {"1": True, "2": True}, measured
    events = records(result)
    assert len(events) == measured["stats"]["delivered_count"] == 1
    assert events[0]["event"] == "request.completed"
    assert measured["stats"]["dropped_count"] == 0
    assert measured["stats"]["sink_state"] == "closed"


@pytest.mark.parametrize("closed_fds", [(1,), (0, 1, 2)])
def test_missing_stdout_remains_nonfatal_and_redirects_survive_close(
    tmp_path: Path,
    closed_fds: tuple[int, ...],
) -> None:
    evidence = tmp_path / "disabled.json"
    code = f"""
import json, os, stat
from dataclasses import asdict
from pathlib import Path
from boxie.observability.logging import LogSettings, bootstrap_logging
for fd in {closed_fds!r}: os.close(fd)
runtime = bootstrap_logging(LogSettings(service="boxie", role="api"))
runtime.emit("request.completed")
runtime.close()
assert all(stat.S_ISCHR(os.fstat(fd).st_mode) for fd in (1, 2))
os.write(1, b"SENTINEL"); os.write(2, b"SENTINEL")
Path({str(evidence)!r}).write_text(json.dumps(asdict(runtime.stats)))
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0
    assert result.stdout == result.stderr == ""
    stats = json.loads(evidence.read_text())
    assert stats["sink_state"] == "unavailable"
    assert stats["delivered_count"] == 0 and stats["dropped_count"] == 1
    assert dict(stats["drops"])["disabled"] == 1


@pytest.mark.parametrize(
    "module,service,role",
    [
        ("boxie.observability.logging", "boxie", "api"),
        ("playground.observability", "cli-playground", "gateway"),
    ],
)
def test_request_boundaries_isolate_reset_and_sanitize_context(
    module: str,
    service: str,
    role: str,
) -> None:
    code = f"""
import asyncio, importlib
from uuid import UUID
from service_telemetry.context import bind_context, current_context
assembly = importlib.import_module({module!r})
runtime = assembly.bootstrap_logging(assembly.LogSettings(service={service!r}, role={role!r}))
secret = {SECRET!r}
seen = {{}}
parent_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
async def downstream(scope, receive, send):
    assert scope["headers"] == [(b"x-request-id", secret.encode())]
    assert scope["query_string"] == secret.encode()
    identity = current_context().get("request_id")
    if scope["type"] != "http":
        assert identity == parent_id
        return
    assert str(UUID(identity)) == identity and identity != parent_id
    await asyncio.sleep(.01)
    assert current_context()["request_id"] == identity
    seen[scope["case"]] = identity
    if scope["case"] == "failure": raise RuntimeError(secret)
    if scope["case"] == "cancelled": raise asyncio.CancelledError()
    await send({{"type":"http.response.start","status":200}})
    await send({{"type":"http.response.body","body":b"ok"}})
app = assembly.RequestLogging(downstream)
async def request(case):
    received = []
    async def receive(): return {{"type":"http.request","body":secret.encode()}}
    async def send(message): received.append(message)
    scope = {{"type":"websocket" if case == "websocket" else "http", "case":case,
             "headers":[(b"x-request-id",secret.encode())], "query_string":secret.encode()}}
    with bind_context(request_id=parent_id):
        try:
            await app(scope, receive, send)
        except RuntimeError as error:
            assert case == "failure" and error.args == (secret,)
        except asyncio.CancelledError:
            assert case == "cancelled"
        else:
            assert case not in ("failure","cancelled")
            if case != "websocket": assert received[0]["status"] == 200
        assert current_context()["request_id"] == parent_id
    assert "request_id" not in current_context()
async def run():
    await asyncio.gather(*(request(case) for case in ("success", "other", "failure", "cancelled", "websocket")))
    assert len(set(seen.values())) == 4
asyncio.run(run())
runtime.emit("request.completed")
runtime.close()
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=5)
    events = records(result)
    assert SECRET not in result.stdout
    assert all(event["service"] == service and event["role"] == role for event in events)
    assert len(events) == 5  # Successes, failure, cancellation, and an unbound event.
    correlated = [event for event in events if "request_id" in event]
    assert len(correlated) == len({event["request_id"] for event in correlated}) == 4
    assert "request_id" not in events[-1]
    failures = [event for event in events if event.get("outcome") == "failed"]
    assert len(failures) == 2
    assert any(event.get("error_type") == "RuntimeError" for event in failures)
    assert all(
        "duration_ms" in event for event in correlated if event["event"] == "request.completed"
    )


@pytest.mark.parametrize("role,fail", [("api", False), ("worker", False), ("agent", True)])
def test_boxie_process_roots_share_shutdown_deadline(
    role: str, fail: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import importlib
    import os
    import threading

    from boxie.observability.metrics import Metrics
    from service_telemetry.logging import LogRuntime, LogSettings
    from service_telemetry.metrics import MetricsListener

    root = importlib.import_module("boxie.bootstrap." + role)
    read, write = os.pipe()
    os.set_blocking(write, False)
    try:
        while True:
            os.write(write, b"x" * 4096)
    except BlockingIOError:
        pass
    runtime = LogRuntime(LogSettings(service="boxie", role=role), {}, {}, write, 4096, "enabled")
    runtime.emit("request.completed")
    entered, finished = threading.Event(), threading.Event()
    listeners: list[MetricsListener] = []
    original_run = MetricsListener._run
    original_close = MetricsListener.close

    def slow_listener(self: MetricsListener) -> None:
        listeners.append(self)
        entered.set()
        try:
            original_run(self)
        finally:
            time.sleep(3)  # Longer than the entire owner budget.
            finished.set()

    def off_loop_close(self: MetricsListener, timeout: float = 2) -> None:
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        original_close(self, timeout)

    def service(metrics: Metrics) -> None:
        assert metrics.role == role

        async def run() -> None:
            assert await asyncio.to_thread(entered.wait, 1)
            if fail:
                raise RuntimeError("service exit")

        asyncio.run(run())

    monkeypatch.setattr(MetricsListener, "_run", slow_listener)
    monkeypatch.setattr(MetricsListener, "close", off_loop_close)
    monkeypatch.setattr("boxie.observability.logging.process_logging", lambda role: runtime)
    monkeypatch.setattr(root, "_main", service)
    started = time.monotonic()
    try:
        if fail:
            with pytest.raises(SystemExit) as failure:
                root.main()
            assert failure.value.code == 1
        else:
            root.main()
        elapsed = time.monotonic() - started
        assert 1.8 <= elapsed < 2.5, elapsed
        # The listener is deliberately still finishing; neither repeated close
        # may wait on a fresh budget or obscure the original process failure.
        assert not finished.is_set()
        repeated = time.monotonic()
        listeners[0].close()
        runtime.close()
        assert time.monotonic() - repeated < 0.2
        assert finished.wait(2)
    finally:
        runtime.close(0)
        os.close(read)
        assert finished.wait(2)
