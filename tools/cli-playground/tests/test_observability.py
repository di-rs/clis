"""Playground owns its assembly and entrypoint independently of Boxie."""

import json
import subprocess
import sys


def test_independent_json_producer() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import asyncio, sys
from playground.observability import LogSettings, RequestLogging, bootstrap_logging
from service_logging.context import current_context
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
    assert event["event"] == "request_completed" and "request_id" in event
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
