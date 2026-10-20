"""Real-process privacy, context, CLI and blocked-sink behavior."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

PRELUDE = """
import asyncio, json, logging, os, sys, threading, time
from pathlib import Path
from dataclasses import asdict
from uuid import UUID
from boxie.observability.logging import LogSettings, configure_logging, bootstrap_logging
from boxie.observability.context import bind_context
dispatcher = bootstrap_logging(LogSettings(service="boxie", role="api"))
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
        (
            Path(__file__).parents[3] / "packages/boxie/infra/observability/event.schema.json"
        ).read_text()
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
    assert all(len(line.encode()) + 1 <= 8192 for line in lines)
    return events


def test_json_context_isolation_and_terminal_close() -> None:
    result = child("""
assert configure_logging(dispatcher.settings) is dispatcher
assert configure_logging(LogSettings(service="boxie", role="supervisor")) is dispatcher
async def task(number):
    with bind_context(request_id=str(UUID(int=number))):
        await asyncio.sleep(0)
        dispatcher.emit("request.completed", duration_ms=number)
async def run(): await asyncio.gather(task(1), task(2))
asyncio.run(run())
dispatcher.emit("request.completed")
assert dispatcher.stats.configuration_errors == 1
dispatcher.close()
assert configure_logging(dispatcher.settings) is dispatcher
assert dispatcher.stats.closed
""")
    events = records(result)
    assert [event.get("request_id") for event in events] == [
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
        None,
    ]


def test_foreign_messages_and_exception_chains_are_private() -> None:
    result = child(f"""
secret = {SECRET!r}
class Hostile:
    def __str__(self): raise AssertionError("must never format")
    def __repr__(self): raise AssertionError("must never repr")
logging.getLogger("httpx").warning("GET https://example/?token=%s", secret)
logging.getLogger("httpx").error("GET https://example/?token=%s", secret)
logging.getLogger(secret).error(Hostile(), Hostile())
try:
    try: raise ValueError(secret + "\\nembedded newline")
    except ValueError as e:
        e.add_note(secret)
        raise RuntimeError(secret) from e
except RuntimeError:
    logging.getLogger("uvicorn.error").exception(secret)
    dispatcher.emit("request.completed", error=sys.exception(), request_id=secret, token=secret)
dispatcher.close()
""")
    events = records(result)
    assert SECRET not in result.stdout + result.stderr
    assert {event["level"] for event in events if event["logger"] == "dependency"} == {
        "WARNING",
        "ERROR",
    }
    assert any(event.get("error_type") == "RuntimeError" for event in events)
    assert any(event.get("error_message") == "operation failed" for event in events)


@pytest.mark.parametrize(
    "method,path,ready,status,outcome",
    [
        ("GET", "/healthz", True, 200, None),
        ("GET", "/readyz", True, 200, None),
        ("GET", "/readyz", False, 503, "failed"),
        ("DELETE", "/v1/artifacts/11111111-1111-1111-1111-111111111111", True, 204, "succeeded"),
    ],
)
def test_real_http_probe_logs_preserve_failures_and_metrics(
    method: str, path: str, ready: bool, status: int, outcome: str | None
) -> None:
    result = child(f"""
import httpx
from boxie.api.app import ApiConfig, create_app
from boxie.observability.metrics import Metrics
from boxie_test_support.api import CONSUMER_AUTH, serve
class Ports:
    async def ready(self): return {ready!r}
    async def retire(self, principal, identifier):
        assert principal.consumer_id == "one"
        assert str(identifier) == "11111111-1111-1111-1111-111111111111"
ports = Ports()
metrics = Metrics("api", logs=dispatcher)
app = create_app(
    ApiConfig(release="test", consumers={{"one":b"consumer-one-secret"}}),
    ports, ports, ports, request_completed=metrics.http_completed,
)
async def run():
    async with serve(app) as origin:
        async with httpx.AsyncClient(base_url=origin, headers=CONSUMER_AUTH) as http:
            response = await http.request({method!r}, {path!r})
            assert response.status_code == {status}
asyncio.run(run())
route = {path!r} if {method!r} == "GET" else "/v1/artifacts/{{artifact_id}}"
sample = f'boxie_http_requests_total{{{{method="{method}",route="{{route}}",status_class="{status // 100}xx"}}}} 1.0'
assert sample.encode() in metrics.render()
dispatcher.close()
""")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    if outcome is None:
        assert result.stdout == ""
    else:
        events = records(result)
        assert len(events) == 1
        assert events[0]["event"] == "request.completed"
        assert events[0]["outcome"] == outcome
        assert events[0]["level"] == ("ERROR" if outcome == "failed" else "INFO")


def test_factory_import_does_not_take_over_streams() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import boxie.bootstrap.api; print('intact')"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0
    assert result.stdout == "intact\n"


@pytest.mark.parametrize("role", ["api", "supervisor"])
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


def test_file_sink_and_standard_streams_remain_usable(tmp_path: Path) -> None:
    output = tmp_path / "events.jsonl"
    with output.open("w") as sink:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                PRELUDE
                + """
assert os.get_blocking(1)
os.write(2, b"terminal remains usable\\n")
dispatcher.emit("request.completed")
dispatcher.close()
""",
            ],
            stdout=sink,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
    assert result.returncode == 0
    assert result.stderr == "terminal remains usable\n"
    assert json.loads(output.read_text())["event"] == "request.completed"


@pytest.mark.parametrize("role", ["api", "supervisor"])
@pytest.mark.parametrize(
    "arguments,status,stream",
    [(["--help"], 0, "stdout"), ([], 2, "stderr"), (["--unknown"], 2, "stderr")],
)
def test_cli_help_and_argument_errors_are_visible(
    role: str, arguments: list[str], status: int, stream: str
) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "boxie.bootstrap." + role, *arguments],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == status
    assert "usage:" in getattr(result, stream)


def test_unicode_messages_and_context_ids_stay_intact() -> None:
    code = """
from boxie.observability.telemetry.logging import bootstrap_logging, LogSettings
from boxie.observability.telemetry.context import bind_context
dispatcher = bootstrap_logging(LogSettings(service="boxie", role="api"), events={"multiline": "é\\n" * 64})
with bind_context(request_id="11111111-1111-1111-1111-111111111111", workflow_id="boxie/sandbox/11111111-1111-1111-1111-111111111111"):
    dispatcher.emit("multiline")
dispatcher.close()
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=5)
    events = records(result)
    assert isinstance(events[0]["message"], str)
    assert "\n" in events[0]["message"]
    assert events[0]["request_id"] == "11111111-1111-1111-1111-111111111111"


def test_oversize_record_is_dropped_whole(tmp_path: Path) -> None:
    import os

    from boxie.observability.telemetry.logging import LogDispatcher, LogSettings

    output = tmp_path / "events.jsonl"
    with output.open("wb") as sink:
        dispatcher = LogDispatcher(
            LogSettings(service="boxie", role="api"), {}, {}, os.dup(sink.fileno()), 512, "enabled"
        )
        try:
            identifier = "11111111-1111-1111-1111-111111111111"
            dispatcher.emit(
                "request.completed",
                request_id=identifier,
                sandbox_id=identifier,
                operation_id=identifier,
                run_id=identifier,
                workflow_id="boxie/sandbox/" + identifier,
            )
        finally:
            dispatcher.close()
    assert output.read_bytes() == b""
    assert dispatcher.stats.dropped_count == 1
    assert dispatcher.stats.queued_records == 0


def test_thread_asyncio_and_unraisable_hooks() -> None:
    result = child(f"""
import gc
from boxie.observability.telemetry.logging import install_asyncio_handler
secret = {SECRET!r}
def fail(): raise ValueError(secret)
thread = threading.Thread(target=fail); thread.start(); thread.join()
class Unraisable:
    def __del__(self): raise KeyError(secret)
Unraisable(); gc.collect()
async def run():
    install_asyncio_handler(dispatcher)
    asyncio.get_running_loop().call_exception_handler({{"message":secret,"exception":RuntimeError(secret)}})
asyncio.run(run())
dispatcher.close()
""")
    events = records(result)
    assert {event["event"] for event in events} == {
        "thread_failed",
        "unraisable_failed",
        "async_failed",
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
    dispatcher.emit("request.completed")
    if dispatcher.stats.sink_state == "broken": break
    time.sleep(.01)
dispatcher.close()
Path({str(evidence)!r}).write_text(json.dumps(asdict(dispatcher.stats)))
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
    # Full Temporal + supervisor/WebSocket path is exercised separately by the live fixture.
    code = (
        PRELUDE
        + f'''
import httpx, pty, tty
from boxie.api.app import ApiConfig, create_app
from boxie_test_support.api import serve
class Ports:
    async def ready(self): return True
ports = Ports()
app = create_app(ApiConfig(release="test", consumers={{"one":b"consumer-one-secret"}}), ports, ports, ports)
async def run():
    async with serve(app) as origin:
        for _ in range(12000): dispatcher.emit("request.completed")
        assert dispatcher.stats.dropped_count > 0
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
    Path({str(evidence)!r}).write_text(json.dumps({{"terminal_roundtrip_completed":True,"drops":dispatcher.stats.dropped_count}}))
asyncio.run(run())
dispatcher.close(.1)
assert dispatcher.stats.closed and dispatcher.stats.queued_records == 0
dropped = dispatcher.stats.dropped_count
dispatcher.emit("request.completed")
dispatcher.close(.1)
assert dispatcher.stats.dropped_count == dropped + 1
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
    replays it. Only the supervisor's Docker port is substituted by that fixture.
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
dispatcher = process_logging("supervisor")
import pytest
from test_outbox import test_committed_command_recovers_without_api_delivery
settings = json.loads(Path({str(config)!r}).read_text())
fixture = SimpleNamespace(address=settings["address"], root=Path(settings["root"]))
# Existing caplog check is supplemented by the parent's actual output check.
with pytest.MonkeyPatch.context() as patch:
    test_committed_command_recovers_without_api_delivery(
        settings["dsn"], fixture, Path({str(scenario_root)!r}), False, patch
    )
dispatcher.emit("request.completed", outcome="succeeded")
dispatcher.close()
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=150
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert events and all(event["schema_version"] == 1 for event in events)
    logs = result.stdout + result.stderr
    for sentinel in ("SECRET-", "consumer-one-secret", "supervisor-test-secret"):
        assert sentinel not in logs
    assert any(
        event["event"] == "request.completed" and event.get("outcome") == "succeeded"
        for event in events
    )


@pytest.mark.parametrize(
    "module,service,role",
    [
        ("boxie.observability.logging", "boxie", "api"),
        ("boxie.observability.logging", "boxie", "supervisor"),
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
from boxie.observability.telemetry.context import bind_context, current_context
assembly = importlib.import_module({module!r})
dispatcher = assembly.bootstrap_logging(assembly.LogSettings(service={service!r}, role={role!r}))
secret = {SECRET!r}
seen = {{}}
parent_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
async def downstream(scope, receive, send):
    assert scope["headers"] == [(b"x-request-id", secret.encode())]
    assert scope["query_string"] == secret.encode()
    identity = current_context().get("request_id")
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
    assert len(set(seen.values())) == 5
asyncio.run(run())
dispatcher.emit("request.completed")
dispatcher.close()
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=5)
    events = records(result)
    assert SECRET not in result.stdout
    assert all(event["service"] == service and event["role"] == role for event in events)
    assert len(events) == 6  # HTTP/WebSocket successes, failure, cancellation, and unbound event.
    correlated = [event for event in events if "request_id" in event]
    assert len(correlated) == len({event["request_id"] for event in correlated}) == 5
    assert "request_id" not in events[-1]
    failures = [event for event in events if event.get("outcome") == "failed"]
    assert len(failures) == 2
    assert any(event.get("error_type") == "RuntimeError" for event in failures)
    assert all(
        "duration_ms" in event for event in correlated if event["event"] == "request.completed"
    )


def test_authenticated_request_id_round_trip_and_error_reset() -> None:
    result = child("""
import httpx
from boxie.api.app import ApiConfig, create_app
from boxie.observability.telemetry.context import current_context
class Ports:
    async def ready(self): return True
ports = Ports()
app = create_app(ApiConfig(release="test", consumers={"one": b"secret"}), ports, ports, ports)
@app.get("/explode")
async def explode(): raise RuntimeError("PRIVATE_SENTINEL")
async def run():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app, raise_app_exceptions=False), base_url="http://test") as client:
        chosen = "11111111-1111-1111-1111-111111111111"
        for path, status in (("/readyz", 200), ("/missing", 404), ("/v1/sandboxes/not-uuid", 422), ("/explode", 500)):
            response = await client.get(path, headers={"Authorization": "Bearer secret", "X-Request-ID": chosen})
            assert response.status_code == status
            assert response.headers["x-request-id"] == chosen
            assert "request_id" not in current_context()
        for headers in (
            [("Authorization", "Bearer secret")],
            [("Authorization", "Bearer secret"), ("X-Request-ID", "PRIVATE_SENTINEL")],
            [("Authorization", "Bearer secret"), ("X-Request-ID", chosen), ("X-Request-ID", chosen)],
            [("Authorization", "Bearer wrong"), ("X-Request-ID", chosen)],
        ):
            response = await client.get("/readyz", headers=headers)
            assert str(UUID(response.headers["x-request-id"])) == response.headers["x-request-id"] != chosen
asyncio.run(run())
dispatcher.close()
""")
    events = records(result)
    assert "PRIVATE_SENTINEL" not in result.stdout + result.stderr
    assert any(
        event.get("request_id") == "11111111-1111-1111-1111-111111111111" for event in events
    )


def test_request_context_replaces_clears_and_restores() -> None:
    from boxie.observability.telemetry import context

    first = "11111111-1111-1111-1111-111111111111"
    with context.bind_context(request_id=first, sandbox_id=first):
        with context.request_context(None):
            assert context.current_request_id() is None
            assert context.current_context()["sandbox_id"] == first
        assert context.current_request_id() == first
    assert context.current_request_id() is None
