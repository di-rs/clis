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


def test_collection_observer_reaches_actual_orchestrator_bootstrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import runpy
    import sys

    from boxie.bootstrap import config, orchestrator
    from boxie.observability.telemetry import logging as producer
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

    monkeypatch.setattr(sys, "argv", ["orchestrator_fixture.py", "--config", "/unused"])
    monkeypatch.setattr(
        config.OrchestratorConfig,
        "from_file",
        lambda _: SimpleNamespace(
            lifecycle=SimpleNamespace(postgres_dsn_file="unused"), data_dir=tmp_path
        ),
    )
    monkeypatch.setattr(orchestrator, "database_for", pool)
    monkeypatch.setattr(PostgresArtifactStore, "__init__", inspect_store)
    monkeypatch.setattr(producer, "install_asyncio_handler", lambda _: None)
    monkeypatch.setattr(producer, "log_runtime", lambda: None)
    monkeypatch.setattr(orchestrator, "main", lambda: orchestrator._main(None, Path("/unused")))  # type: ignore[arg-type]
    # Restore the test wrapper's module substitution after exercising the real _main import.
    monkeypatch.setattr(
        "boxie.persistence.postgres_artifacts.PostgresArtifactStore", PostgresArtifactStore
    )
    with pytest.raises(ReachedStore):
        runpy.run_path(
            str(ROOT / "tools/boxie/tests/integration/orchestrator_fixture.py"), run_name="__main__"
        )
    assert observed == ["ObservedArtifacts"]


def test_native_journal_check_requires_structured_orchestrator_error() -> None:
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
            "runtime = bootstrap_logging(LogSettings(service='boxie', role='orchestrator'))\n"
            "logging.getLogger('boxie.sandbox.execution.orchestrator').error("
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


@pytest.mark.parametrize("still_exists", [False, True])
def test_children_tolerates_only_confirmed_concurrent_deletion(
    tmp_path: Path, still_exists: bool
) -> None:
    from integration.stack import LifecycleStack

    identifier = "a" * 64
    calls: list[list[str]] = []

    def run(args: list[str], *, env: dict[str, str] | None = None) -> str:
        calls.append(args)
        if args[1] == "inspect":
            raise RuntimeError("inspect failed")
        if "id=" + identifier in args:
            return identifier if still_exists else ""
        return identifier

    stack = LifecycleStack(tmp_path, command_runner=run)
    if still_exists:
        with pytest.raises(RuntimeError, match="inspect failed"):
            stack.children()
    else:
        assert stack.children() == []
    assert "--no-trunc" in calls[0]
    assert calls[-1][-1] == "id=" + identifier


def test_readiness_probe_exports_only_allowlisted_booleans(monkeypatch: pytest.MonkeyPatch) -> None:
    import contextlib
    import io
    import json

    from integration.diagnostics import ORCHESTRATOR_PROBE

    original_client = httpx.Client
    secret = "secret-do-not-export"

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/readyz":
            return httpx.Response(
                503,
                json={
                    "dependencies": {
                        "docker": True,
                        "postgres": False,
                        "execution": False,
                        "private": secret,
                    },
                    "private": secret,
                },
            )
        return httpx.Response(
            200,
            text='boxie_dependency_ready{dependency="orchestrator"} 0\nboxie_dependency_known{dependency="orchestrator"} 1\nprivate_metric{secret="'
            + secret
            + '"} 7\n',
        )

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    monkeypatch.setattr(Path, "read_text", lambda self: secret)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exec(ORCHESTRATOR_PROBE, {})
    assert secret not in output.getvalue()
    data = json.loads(output.getvalue())
    assert data["orchestrator"] == {
        "status": 503,
        "dependencies": {"docker": True, "postgres": False, "execution": False},
    }
    assert data["dependencies"]["boxie_dependency_ready"]["orchestrator"] == 0


def test_readiness_details_exclude_container_error_and_configuration_secrets() -> None:
    import json

    from integration.diagnostics import readiness_details

    secret = "credential-and-private-error"
    item = {
        "Config": {
            "Labels": {
                "com.docker.compose.project": "owned",
                "com.docker.compose.service": "orchestrator",
            },
            "Env": [secret],
        },
        "State": {
            "Status": "exited",
            "Running": False,
            "OOMKilled": True,
            "ExitCode": 137,
            "Error": secret,
        },
    }
    stack = SimpleNamespace(
        runtime="owned",
        runtime_command=lambda *args: "a" * 64 if args[0] == "ps" else "{}",
        command=lambda args: json.dumps([item]),
    )
    output = readiness_details(stack)
    assert secret not in output
    state = json.loads(output)["containers"]["orchestrator"]
    assert state["oom_killed"] is True and state["exit_code"] == 137 and state["running"] is False
    assert json.loads(output)["containers"]["faults"] == {"error_type": "ValueError"}


def test_assembled_fixture_prepares_committed_production_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    import subprocess

    from integration.deployment import DeploymentStack

    stack = DeploymentStack(tmp_path, monitoring=False)
    stack.images["boxie"] = "registry.example/boxie@sha256:" + "a" * 64
    stack.env.update(json.loads((ROOT / "tools/boxie/infra/settings.example.json").read_text()))
    stack.env["BOXIE_MONITORING"] = "false"
    stack.env["BOXIE_TEST_API_PORT"] = "18090"
    original = subprocess.run
    captured: list[dict[str, Any]] = []

    def execute(argv: list[str], *args: Any, **kwargs: Any) -> Any:
        if len(argv) > 1 and str(argv[1]).endswith("deploy_tool/remote.py"):
            payload = json.loads(kwargs["input"])
            captured.append(payload)
            return subprocess.CompletedProcess(argv, 0, b'{"outcome":"activated"}', b"")
        return original(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", execute)
    assert stack.release("boxie")["outcome"] == "activated"
    assert len(captured) == 1
    payload = captured[0]
    assert payload["descriptor"]["services"] == ["api", "orchestrator"]
    assert payload["release"]["settings"]["BOXIE_MONITORING"] == "false"
    assert not any(key.startswith("PLAYGROUND_") for key in payload["environment"])
    assert len(payload["release"]["configuration_hashes"]) == 4
