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
import sys
from playground.observability import LogSettings, bootstrap_logging
runtime = bootstrap_logging(LogSettings(service="cli-playground", role="gateway"))
runtime.emit("request_completed")
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
