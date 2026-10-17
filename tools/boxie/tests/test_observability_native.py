"""Small upstream validators and a Linux journal-to-dashboard integration smoke."""

import hashlib
import io
import json
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path
from string import Template
from typing import Any
from uuid import uuid4

import httpx
import pytest

OBS = Path(__file__).parents[1] / "infra/observability"
PINS = json.loads((OBS / "versions.json").read_text())
VECTOR = PINS["collector"]["image"]


def docker(*args: str, input: str | None = None) -> str:
    result = subprocess.run(
        ["docker", *args], input=input, text=True, capture_output=True, timeout=90
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return (result.stdout + result.stderr).strip() if args[0] == "logs" else result.stdout.strip()


def substituted(source: Path, destination: Path) -> None:
    destination.write_text(
        Template(source.read_text()).substitute(
            BOXIE_INSTALLATION_ID="00000000-0000-0000-0000-000000000001",
            BOXIE_HOST_ID="test-host",
            BOXIE_TEMPORAL_NAMESPACE="boxie",
            OPERATOR_TUNNEL_ID="00000000-0000-0000-0000-000000000002",
            OPERATOR_TEAM_NAME="operators",
            OPERATOR_GRAFANA_HOSTNAME="grafana.example.com",
            OPERATOR_TEMPORAL_HOSTNAME="temporal.example.com",
            OPERATOR_GRAFANA_AUDIENCE="grafana-aud",
            OPERATOR_TEMPORAL_AUDIENCE="temporal-aud",
        )
    )


@pytest.mark.integration
def test_native_configs(tmp_path: Path) -> None:
    mount = f"{OBS}:/config:ro"
    for command in (["validate", "--no-environment", "--skip-healthchecks"], ["test"]):
        docker(
            "run",
            "--rm",
            "--user",
            "65532:65532",
            "-e",
            "BOXIE_ENVIRONMENT=test",
            "-e",
            "BOXIE_HOST_ID=test-host",
            "-v",
            mount,
            VECTOR,
            *command,
            "/config/vector.yaml",
        )
    substituted(OBS / "prometheus.yml", tmp_path / "prometheus.yml")
    for args in (
        ["check", "config", "/config/prometheus.yml"],
        ["check", "rules", "/config/rules/boxie.yml"],
        ["test", "rules", "/config/rules/tests.yml"],
    ):
        docker(
            "run",
            "--rm",
            "--user",
            "0",
            "-v",
            mount,
            "-v",
            f"{tmp_path}/prometheus.yml:/config/prometheus.yml:ro",
            "-w",
            "/config",
            "--entrypoint",
            "promtool",
            PINS["images"]["prometheus"]["image"],
            *args,
        )
    substituted(OBS.parent / "ingress/cloudflared.yml", tmp_path / "cloudflared.yml")
    docker(
        "run",
        "--rm",
        "--user",
        "0",
        "-v",
        f"{tmp_path}:/config:ro",
        PINS["images"]["cloudflared"]["image"],
        "tunnel",
        "--config",
        "/config/cloudflared.yml",
        "ingress",
        "validate",
    )


def scenario(postgres_dsn: str, temporal_stack: Any, tmp_path: Path, request_id: str) -> str:
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "dsn": postgres_dsn,
                "address": temporal_stack.address,
                "root": str(temporal_stack.root),
            }
        )
    )
    settings.chmod(0o600)
    return f"""
import sys, json, pytest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, {str(Path(__file__).parent)!r})
from boxie.observability.logging import process_logging
from boxie.observability.context import bind_context
from test_outbox import test_busy_maintenance_preserves_terminal_and_lease_and_cleanup_history
from test_observability_lifecycle import test_committed_outcomes_and_rebuilt_gauges
runtime = process_logging("api")
settings = json.loads(Path({str(settings)!r}).read_text())
with pytest.MonkeyPatch.context() as patch, bind_context(request_id={request_id!r}):
    test_busy_maintenance_preserves_terminal_and_lease_and_cleanup_history(settings["dsn"], SimpleNamespace(address=settings["address"], root=Path(settings["root"])), Path({str(tmp_path / "terminal")!r}), patch)
    test_committed_outcomes_and_rebuilt_gauges(settings["dsn"], Path({str(tmp_path / "failed")!r}), "failed")
runtime.close()
"""


@pytest.mark.integration
def test_lifecycle_diagnostic_source(
    postgres_dsn: str, temporal_stack: Any, tmp_path: Path
) -> None:
    request_id = str(uuid4())
    result = subprocess.run(
        [sys.executable, "-c", scenario(postgres_dsn, temporal_stack, tmp_path, request_id)],
        capture_output=True,
        text=True,
        timeout=150,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert any(event["event"] == "terminal.attached" for event in events)
    assert any(
        event["event"] == "provisioning.completed"
        and event.get("request_id") == request_id
        and event.get("outcome") == "failed"
        for event in events
    )
    assert "consumer-one-secret" not in result.stdout


@pytest.mark.integration
def test_failed_lifecycle_is_searchable_after_collector_outage(
    postgres_dsn: str,
    temporal_stack: Any,
    tmp_path: Path,
) -> None:
    if sys.platform != "linux" or not Path("/var/log/journal").is_dir():
        pytest.fail("journal smoke requires a Linux Docker host with persistent journald")
    import grp

    name = "boxie-obs-" + uuid4().hex[:12]
    containers: list[str] = []
    request_id = str(uuid4())
    network = docker("network", "create", "--label", f"boxie.test={name}", name)

    def start(role: str, image: str, *args: str, command: tuple[str, ...] = ()) -> str:
        container = name + "-" + role
        docker(
            "run",
            "-d",
            "--name",
            container,
            "--label",
            f"boxie.test={name}",
            "--network",
            network,
            *args,
            image,
            *command,
        )
        containers.append(container)
        return container

    try:
        logs = start(
            "logs",
            PINS["images"]["victorialogs"]["image"],
            "--network-alias",
            "victorialogs",
            "--publish",
            "127.0.0.1::9428",
            command=(
                "-storageDataPath=/tmp/logs",
                "-retentionPeriod=7d",
                "-retention.maxDiskSpaceUsageBytes=268435456",
            ),
        )
        vector = start(
            "vector",
            VECTOR,
            "--user",
            "65532:65532",
            "--group-add",
            str(grp.getgrnam("systemd-journal").gr_gid),
            "--tmpfs",
            "/var/lib/vector:uid=65532,gid=65532",
            "-v",
            f"{OBS}/vector.yaml:/etc/vector/vector.yaml:ro",
            "-v",
            "/var/log/journal:/var/log/journal:ro",
            "-v",
            "/etc/machine-id:/etc/machine-id:ro",
            "-e",
            "BOXIE_ENVIRONMENT=test",
            "-e",
            "BOXIE_HOST_ID=test-host",
        )
        docker("stop", "--time", "3", vector)
        # Run existing real SQL/Temporal, HTTP/WebSocket, lease/cleanup and failed
        # lifecycle assertions while the collector is stopped. Docker is the
        # existing fixture's substituted boundary; this is not a gVisor claim.
        code = scenario(postgres_dsn, temporal_stack, tmp_path, request_id)
        producer = name + "-producer"
        emitter = subprocess.Popen(
            [
                "docker",
                "run",
                "--rm",
                "-i",
                "--name",
                producer,
                "--log-driver",
                "journald",
                "--log-opt",
                "tag=boxie-api",
                "--entrypoint",
                "/bin/cat",
                VECTOR,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            result = subprocess.run(
                [sys.executable, "-c", code],
                stdout=emitter.stdin,
                stderr=subprocess.PIPE,
                timeout=150,
            )
            assert result.returncode == 0, result.stderr.decode()
            assert emitter.stdin is not None
            emitter.stdin.close()
            assert emitter.wait(timeout=10) == 0
        finally:
            subprocess.run(["docker", "rm", "-f", producer], capture_output=True, timeout=15)
            if emitter.poll() is None:
                emitter.kill()
            emitter.wait(timeout=5)
            if emitter.stderr is not None:
                emitter.stderr.close()
        docker("start", vector)
        # Install the pinned datasource and query it through actual Grafana.
        pin = PINS["plugins"]["victoriametrics-logs-datasource"]
        with urllib.request.urlopen(pin["url"], timeout=30) as source:
            archive = source.read()
        assert hashlib.sha256(archive).hexdigest() == pin["sha256"]
        plugins = tmp_path / "plugins"
        plugins.mkdir()
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            zipped.extractall(plugins)
            for member in zipped.infolist():
                path = plugins / member.filename
                executable = bool((member.external_attr >> 16) & 0o111)
                path.chmod(0o755 if path.is_dir() or executable else 0o644)
        grafana = start(
            "grafana",
            PINS["images"]["grafana"]["image"],
            "--publish",
            "127.0.0.1::3000",
            "--memory",
            "268435456",
            "-e",
            "GF_SECURITY_ADMIN_PASSWORD=smoke-only-password",
            "-e",
            "GOGC=25",
            "-e",
            "GF_PLUGINS_PREINSTALL_DISABLED=true",
            "-v",
            f"{plugins}:/var/lib/grafana/plugins:ro",
            "-v",
            f"{OBS}/grafana/provisioning:/etc/grafana/provisioning:ro",
            "-v",
            f"{OBS}/grafana/dashboards:/etc/grafana/dashboards:ro",
        )
        origin = "http://" + docker("port", grafana, "3000/tcp")
        query = (
            f'request_id:="{request_id}" AND event:="provisioning.completed" AND outcome:="failed"'
        )
        with httpx.Client(
            auth=("admin", "smoke-only-password"), timeout=2, trust_env=False
        ) as client:
            for _ in range(60):
                try:
                    response = client.get(
                        origin
                        + "/api/datasources/proxy/uid/boxie-victorialogs/select/logsql/query",
                        params={"query": query},
                    )
                    if response.is_success and request_id in response.text:
                        rows = [json.loads(line) for line in response.text.splitlines()]
                        assert any(
                            row.get("sandbox_id") and row.get("outcome") == "failed" for row in rows
                        )
                        assert "consumer-one-secret" not in response.text
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.5)
            else:
                pytest.fail(
                    "failed lifecycle not searchable via Grafana: "
                    + docker("logs", vector)
                    + docker("logs", logs)
                    + docker("logs", grafana)
                )
    finally:
        for container in reversed(containers):
            docker("rm", "--force", "--volumes", container)
        docker("network", "rm", network)


@pytest.mark.integration
def test_native_storage_ownership(tmp_path: Path) -> None:
    from test_observability_config import compose

    model = compose(OBS / "compose.yml")
    model["name"] = "boxie-storage-" + uuid4().hex[:12]
    model["services"] = {
        key: value for key, value in model["services"].items() if key == "storage-init"
    }
    model["networks"] = {}
    for key, volume in model["volumes"].items():
        volume["name"] = model["name"] + "-" + key
    manifest = tmp_path / "storage.json"
    manifest.write_text(json.dumps(model))
    try:
        if model["services"]:
            docker(
                "compose",
                "-f",
                str(manifest),
                "up",
                "--abort-on-container-exit",
                "--exit-code-from",
                "storage-init",
            )
        for key, uid in [("vector-data", "65532:65532"), ("logs-data", "65534:65534")]:
            docker(
                "run",
                "--rm",
                "--user",
                uid,
                "-v",
                model["volumes"][key]["name"] + ":/state",
                "--entrypoint",
                "/bin/sh",
                VECTOR,
                "-ec",
                "echo checkpoint > /state/check; test -r /state/check",
            )
    finally:
        docker("compose", "-f", str(manifest), "down", "--volumes")
