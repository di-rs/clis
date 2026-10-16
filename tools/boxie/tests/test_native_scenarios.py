"""Focused fixture regressions; Docker transport is not a native runsc proof."""

import socket
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from string import Template
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import pytest
import yaml
from run_integration import ROOT, ProductionStack, command


def test_collection_observer_reaches_actual_agent_bootstrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import runpy
    import sys

    from boxie.bootstrap import agent, config, durable
    from boxie.persistence.postgres_artifacts import PostgresArtifactStore

    class ReachedStore(Exception):
        pass

    @asynccontextmanager
    async def pool(*args: object) -> AsyncIterator[None]:
        yield None

    observed: list[str] = []

    def inspect_store(self: Any, *args: object) -> None:
        observed.append(type(self).__name__)
        raise ReachedStore

    monkeypatch.setattr(sys, "argv", ["agent_fixture.py", "--config", "/unused"])
    monkeypatch.setattr(
        config.AgentConfig,
        "from_file",
        lambda _: SimpleNamespace(postgres_dsn_file="unused", api_url="unused", data_dir=tmp_path),
    )
    monkeypatch.setattr(durable, "pool_for", pool)
    monkeypatch.setattr(PostgresArtifactStore, "__init__", inspect_store)
    monkeypatch.setattr(agent, "install_asyncio_handler", lambda _: None)
    monkeypatch.setattr(agent, "log_runtime", lambda: None)
    monkeypatch.setattr(agent, "main", lambda: agent._main(None))  # type: ignore[arg-type]
    # Restore the test wrapper's module substitution after exercising the real _main import.
    monkeypatch.setattr(
        "boxie.persistence.postgres_artifacts.PostgresArtifactStore", PostgresArtifactStore
    )
    with pytest.raises(ReachedStore):
        runpy.run_path(
            str(ROOT / "tools/boxie/tests/integration/agent_fixture.py"), run_name="__main__"
        )
    assert observed == ["ObservedArtifacts"]


def test_native_journal_check_requires_structured_agent_error() -> None:
    import json
    import subprocess
    import sys

    from integration.test_lifecycle import assert_journal_failure

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import logging\n"
            "from boxie.observability.logging import LogSettings, bootstrap_logging\n"
            "runtime = bootstrap_logging(LogSettings(service='boxie', role='agent'))\n"
            "logging.getLogger('boxie.sandbox.lifecycle').error("
            "'execution journal persistence failed; new execution disabled')\n"
            "runtime.close()\n",
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0 and result.stderr == ""
    assert_journal_failure(result.stdout)
    event = json.loads(result.stdout)
    for changed in ({"level": "INFO"}, {"role": "api"}, {"event": "journal_recovered"}):
        with pytest.raises(AssertionError):
            assert_journal_failure(json.dumps({**event, **changed}))
    with pytest.raises(AssertionError):
        assert_journal_failure(json.dumps({"message": "execution journal persistence failed"}))


@pytest.mark.integration
@pytest.mark.parametrize("service, target", [("api", 8090), ("temporal", 7233)])
def test_native_published_endpoint_survives_process_restart(
    tmp_path: Path, service: str, target: int
) -> None:
    # Exercise the actual native fixture's publish specification against Docker.
    config = yaml.safe_load(
        (ROOT / "tools/boxie/infra/compose.test.yml").read_text().replace("!override", "")
    )
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    publish = Template(config["services"]["api"]["ports"][0]).substitute(
        BOXIE_TEST_API_PORT=str(port)
    )
    if service == "temporal":
        ProductionStack(tmp_path).write_overrides()
        # This read extracts only the port scalar, without applying Compose merges.
        # Keep the actual override file/tag unchanged for Docker Compose itself.
        override = yaml.safe_load(
            (tmp_path / "temporal-override.yml").read_text().replace("!override", "")
        )
        publish = override["services"]["temporal"]["ports"][0]
    name = "boxie-port-" + uuid4().hex
    script = """
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(503)
        self.end_headers()
    def log_message(self, *args): pass
HTTPServer(('0.0.0.0', int(sys.argv[1])), Handler).serve_forever()
"""
    try:
        command(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                name,
                "--publish",
                publish,
                "python:3.15.0-slim",
                "python",
                "-c",
                script,
                str(target),
            ]
        )
        endpoint = command(["docker", "port", name, f"{target}/tcp"])
        with httpx.Client(timeout=1, trust_env=False) as client:
            for _ in range(60):
                try:
                    assert client.get("http://" + endpoint + "/readyz").status_code == 503
                    break
                except httpx.TransportError:
                    time.sleep(0.1)
            else:
                pytest.fail("fixture HTTP dependency status never became reachable")
            command(["docker", "kill", name])
            command(["docker", "start", name])
            restarted = command(["docker", "port", name, f"{target}/tcp"])
            print(f"published {service} endpoint: before={endpoint}, after={restarted}")
            assert restarted == endpoint, "restart invalidated existing host clients"
            for _ in range(60):
                try:
                    assert client.get("http://" + endpoint + "/readyz").status_code == 503
                    break
                except httpx.TransportError:
                    time.sleep(0.1)
            else:
                pytest.fail("restarted endpoint unreachable; this is not a dependency 503")
    finally:
        command(["docker", "rm", "--force", "--volumes", name])


def test_temporal_publish_parser_reaches_docker_without_removing_compose_tags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    class ReachedDocker(Exception):
        pass

    calls: list[list[str]] = []

    def stop_at_docker(args: list[str]) -> str:
        calls.append(args)
        if args[1] == "run":
            raise ReachedDocker
        return ""

    monkeypatch.setattr(sys.modules[__name__], "command", stop_at_docker)
    with pytest.raises(ReachedDocker):
        test_native_published_endpoint_survives_process_restart(tmp_path, "temporal", 7233)
    publish = calls[0][calls[0].index("--publish") + 1]
    assert publish.startswith("127.0.0.1:") and publish.endswith(":7233")
    assert "logging: !override" in (tmp_path / "temporal-override.yml").read_text()
