"""The host composes consumer collection without changing standalone Boxie."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[4]
BOXIE = ROOT / "packages/boxie/infra/observability"
CONSUMER = ROOT / "tools/cli-playground/infra/observability"
OVERLAY = ROOT / "infra/environments/single-host/observability/compose.yml"


def test_host_overlay_resolves_installed_consumer_config(tmp_path: Path) -> None:
    target = tmp_path / "release"
    shutil.copytree(BOXIE, target / "boxie/observability")
    shutil.copytree(CONSUMER, target / "cli-playground/observability")
    shutil.copytree(OVERLAY.parent, target / "observability")
    env = {
        **os.environ,
        "OBS_JOURNAL_GROUP_GID": "190",
        "OBS_GRAFANA_HOSTNAME": "grafana.example.com",
        "OBS_GRAFANA_ADMIN_PASSWORD_FILE": "/tmp/secret",
        "OBS_GRAFANA_PLUGIN_DIR": "/tmp/plugins",
        "OBS_PROMETHEUS_CONFIG_FILE": "/tmp/prometheus",
        "SANCTUARY_CLI_PLAYGROUND_DIR": str(target / "cli-playground"),
    }
    base = ["docker", "compose", "-f", str(target / "boxie/observability/compose.yml")]

    def compose(extra: list[str]) -> dict[str, Any]:
        result = subprocess.run(
            base + extra + ["config", "--format", "json"],
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        return json.loads(result.stdout)  # type: ignore[no-any-return]

    standalone = compose([])
    assert standalone["services"]["vector"]["command"] == ["--config", "/etc/vector/vector.yaml"]
    combined = compose(["-f", str(target / "observability/compose.yml")])
    vector = combined["services"]["vector"]
    assert vector["command"] == [
        "--config",
        "/etc/vector/vector.yaml",
        "--config",
        "/etc/vector/cli-playground.yaml",
    ]
    mount = next(v for v in vector["volumes"] if v["target"] == "/etc/vector/cli-playground.yaml")
    assert Path(mount["source"]).is_file() and mount["read_only"]
    backend = yaml.safe_load(
        (ROOT / "tools/cli-playground/infra/compose.production.yml").read_text()
    )["services"]["backend"]
    assert backend["logging"]["driver"] == "journald"
    assert backend["logging"]["options"]["mode"] == "non-blocking"
    assert backend["logging"]["options"]["tag"] == "cli-playground-api"


@pytest.mark.integration
def test_merged_native_vector_validation() -> None:
    pins = json.loads((BOXIE / "versions.json").read_text())
    for args in (["validate", "--no-environment"], ["test"]):
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "-e",
                "BOXIE_ENVIRONMENT=test",
                "-e",
                "BOXIE_HOST_ID=test",
                "-v",
                f"{BOXIE}:/boxie:ro",
                "-v",
                f"{CONSUMER}:/consumer:ro",
                pins["collector"]["source_image"],
                *args,
                "/boxie/vector.yaml",
                "/consumer/vector.yaml",
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.integration
def test_merged_collector_smoke_after_storage_outage(tmp_path: Path) -> None:
    import time
    from datetime import UTC, datetime
    from uuid import uuid4

    import httpx

    pins = json.loads((BOXIE / "versions.json").read_text())
    identifier = str(uuid4())
    name = "correlation-" + uuid4().hex[:12]
    network, storage, collector = name, name + "-logs", name + "-vector"

    def docker(*args: str) -> str:
        return subprocess.check_output(
            ["docker", *args], text=True, stderr=subprocess.STDOUT, timeout=90
        ).strip()

    config = yaml.safe_load((BOXIE / "vector.yaml").read_text())
    extension = yaml.safe_load((CONSUMER / "vector.yaml").read_text())
    for section in ("sources", "transforms", "sinks"):
        config[section].update(extension[section])
    config.pop("tests")
    config["sources"] = {
        "fixture": {
            "type": "file",
            "include": ["/fixtures/events.jsonl"],
            "read_from": "beginning",
        }
    }
    config["sinks"].pop("metrics")
    for transform in config["transforms"].values():
        transform["inputs"] = ["fixture_decode"]
    config["transforms"]["fixture_decode"] = {
        "type": "remap",
        "inputs": ["fixture"],
        "source": ". = parse_json!(.message)",
    }
    for sink in config["sinks"].values():
        sink["uri"] = sink["uri"].replace("victorialogs:9428", storage + ":9428")
    (tmp_path / "vector.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
    base = {
        "schema_version": 1,
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "level": "INFO",
        "event": "request.completed",
        "message": "Request completed",
        "role": "api",
        "instance_id": str(uuid4()),
        "request_id": identifier,
    }
    events = [
        {
            "CONTAINER_TAG": "boxie-api",
            "message": json.dumps(
                {**base, "service": "boxie", "release": "unknown", "logger": "boxie"}
            ),
        },
        {
            "CONTAINER_TAG": "cli-playground-api",
            "message": json.dumps({**base, "service": "cli-playground"}),
        },
    ]
    (tmp_path / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))
    docker("network", "create", network)
    try:
        docker(
            "run",
            "--detach",
            "--name",
            storage,
            "--network",
            network,
            "--publish",
            "127.0.0.1::9428",
            pins["images"]["victorialogs"]["image"],
        )
        docker("stop", storage)
        docker(
            "run",
            "--detach",
            "--name",
            collector,
            "--network",
            network,
            "--user",
            "0",
            "-e",
            "BOXIE_ENVIRONMENT=test",
            "-e",
            "BOXIE_HOST_ID=test",
            "--tmpfs",
            "/var/lib/vector",
            "-v",
            f"{tmp_path}:/fixtures:ro",
            pins["collector"]["source_image"],
            "--config",
            "/fixtures/vector.yaml",
        )
        time.sleep(2)
        docker("start", storage)
        port = docker("port", storage, "9428/tcp").rsplit(":", 1)[1]
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=2) as http:
            for _ in range(120):
                try:
                    response = http.post(
                        "/select/logsql/query",
                        data={"query": f'request_id:="{identifier}" | fields service'},
                    )
                    response.raise_for_status()
                    services = {json.loads(line)["service"] for line in response.text.splitlines()}
                    if services == {"boxie", "cli-playground"}:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.25)
            else:
                pytest.fail(
                    "correlated events did not reach storage: "
                    + response.text
                    + " all="
                    + http.post("/select/logsql/query", data={"query": "*", "start": "0"}).text
                    + docker("logs", collector)
                )
    finally:
        subprocess.run(
            ["docker", "rm", "--force", "--volumes", collector, storage],
            capture_output=True,
            timeout=30,
        )
        docker("network", "rm", network)
