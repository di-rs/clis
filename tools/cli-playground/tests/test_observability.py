"""Real process logging order, privacy and public request boundaries."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from cli_playground.service import CliPlaygroundService


def test_public_context_and_uvicorn_factory_logging(tmp_path: Path) -> None:
    code = """
import asyncio, json, logging, socket, sys
from pathlib import Path
import httpx, uvicorn
from fastapi import HTTPException, WebSocket
from websockets.asyncio.client import connect
from cli_playground.app import create_app
from cli_playground.config import Settings
from cli_playground.observability import current_request_id, request_context, shutdown_logging
class Client:
    async def close(self): pass
async def run():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        origin = f"http://127.0.0.1:{sock.getsockname()[1]}"
        def factory():
            app = create_app(Settings(data_dir=Path(sys.argv[1]), public_url=origin, deploy_token="private-token", boxie_url="http://127.0.0.1:1", boxie_credential_file=Path(sys.argv[1])/"unused"), boxie=Client())
            @app.get("/fail")
            async def fail():
                logging.getLogger("unknown").error("PRIVATE_SENTINEL %s", "credentials")
                raise RuntimeError("PRIVATE_SENTINEL")
            @app.get("/unavailable")
            async def unavailable():
                raise HTTPException(503, "PRIVATE_SENTINEL")
            @app.websocket("/probe")
            async def probe(ws: WebSocket):
                await ws.accept()
                await ws.send_text(current_request_id())
                await ws.close()
            return app
        server = uvicorn.Server(uvicorn.Config(factory, factory=True, access_log=False, ws="websockets-sansio"))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            for _ in range(300):
                if server.started: break
                await asyncio.sleep(.01)
            assert server.started
            async with httpx.AsyncClient(base_url=origin) as client:
                expected = {}
                for path, status in (("/missing",404), ("/unavailable",503), ("/fail",500)):
                    response = await client.get(path, headers={"X-Request-ID":"11111111-1111-1111-1111-111111111111"})
                    assert response.status_code == status
                    identity = response.headers["x-request-id"]
                    assert identity != "11111111-1111-1111-1111-111111111111"
                    expected[identity] = "ERROR"
                assert len(expected) == 3
                assert (await client.get("/healthz")).status_code == 200
            async with connect(origin.replace("http:","ws:")+"/probe") as ws:
                assert ws.response.headers["x-request-id"] == await ws.recv()
                expected[ws.response.headers["x-request-id"]] = "INFO"
            assert current_request_id() is None
            print(json.dumps(expected), file=sys.stderr)
        finally:
            server.should_exit = True
            await task
asyncio.run(run())
shutdown_logging()
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PRIVATE_SENTINEL" not in result.stdout + result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert events and all(event["service"] == "cli-playground" for event in events)
    completed = [event for event in events if event["event"] == "request.completed"]
    assert len(completed) == 4
    assert {event["request_id"]: event["level"] for event in completed} == json.loads(result.stderr)
    assert all(len(line.encode()) + 1 <= 8192 for line in result.stdout.splitlines())


def test_native_log_check_waits_for_delayed_output() -> None:
    from .test_native import wait_for_request_log

    request_id = "11111111-1111-1111-1111-111111111111"
    outputs = iter(
        ["startup\n", '{"request_id":"unrelated"}', json.dumps({"request_id": request_id})]
    )

    def read_logs(*args: str) -> str:
        return next(outputs)

    wait_for_request_log(read_logs, "api", request_id, timeout=2)
    assert next(outputs, None) is None


def test_native_log_check_times_out_with_the_missing_service() -> None:
    from .test_native import wait_for_request_log

    with pytest.raises(AssertionError, match="supervisor"):
        wait_for_request_log(lambda *args: "", "supervisor", "missing", timeout=0)


def test_blocked_stdout_keeps_queue_and_shutdown_bounded() -> None:
    code = """
import logging
from cli_playground.observability import configure_logging
configure_logging()
for _ in range(20000):
    logging.getLogger("dependency").error("PRIVATE_SENTINEL")
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        assert process.wait(timeout=7) == 0
        stdout, stderr = process.communicate()
        assert b"PRIVATE_SENTINEL" not in stdout + stderr
        assert stderr == b""
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def test_failed_upload_recovery_logs_saved_origin(
    service: CliPlaygroundService, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import logging
    from collections.abc import AsyncIterator
    from uuid import UUID

    from cli_playground.boxie_client import BoxieUnavailable
    from cli_playground.observability import current_request_id, request_context

    from .helpers import binary_bundle, elf_binary

    origins: list[str | None] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            origins.append(current_request_id())

    handler = Capture()
    logger = logging.getLogger("cli_playground.service")

    async def unavailable(identifier: UUID, digest: str, archive: Path) -> None:
        raise BoxieUnavailable()

    monkeypatch.setattr(service.boxie, "upload", unavailable)

    async def chunks() -> AsyncIterator[bytes]:
        yield binary_bundle({"echor": elf_binary()})

    async def exercise() -> None:
        with request_context("11111111-1111-1111-1111-111111111111"):
            with pytest.raises(BoxieUnavailable):
                await service.deploy(chunks(), "a" * 40, 123)
        logger.addHandler(handler)
        try:
            with request_context("22222222-2222-2222-2222-222222222222"):
                await service.recover()
        finally:
            logger.removeHandler(handler)
        assert origins == ["11111111-1111-1111-1111-111111111111"]

    asyncio.run(exercise())


def test_periodic_cleanup_failure_keeps_its_action_id(
    service: CliPlaygroundService, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import logging

    from cli_playground.observability import current_request_id, request_context

    service.settings = service.settings.model_copy(update={"cleanup_interval_seconds": 0})
    actions: list[str | None] = []
    failures: list[str | None] = []

    async def exercise() -> None:
        failed = asyncio.Event()

        class Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                failures.append(current_request_id())
                failed.set()

        async def recover(*, startup: bool = True) -> None:
            actions.append(current_request_id())
            raise OSError("unavailable storage")

        monkeypatch.setattr(service, "recover", recover)
        logger = logging.getLogger("cli_playground.service")
        handler = Capture()
        logger.addHandler(handler)
        inherited = "11111111-1111-1111-1111-111111111111"
        with request_context(inherited):
            task = asyncio.create_task(service.cleanup_periodically())
            try:
                async with asyncio.timeout(1):
                    await failed.wait()
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                logger.removeHandler(handler)
            assert current_request_id() == inherited
        assert actions == failures
        assert actions and all(
            identity is not None and identity != inherited for identity in actions
        )

    asyncio.run(exercise())
