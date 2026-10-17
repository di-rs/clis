"""Integration boundaries for the committed native monitoring configuration."""

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

INFRA = Path(__file__).parents[1] / "infra"


def compose(*files: Path) -> dict[str, Any]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("OBS_", "OPERATOR_", "COMPOSE_"))
    }
    env.update(
        {
            key: "/tmp/boxie-test"
            for key in (
                "BOXIE_API_CONFIG",
                "BOXIE_ORCHESTRATOR_CONFIG",
                "BOXIE_PROFILES_FILE",
                "BOXIE_DATA_DIR",
                "BOXIE_ORCHESTRATOR_CREDENTIAL_FILE",
                "BOXIE_CONSUMER_CREDENTIAL_FILE",
                "BOXIE_POSTGRES_DSN_FILE",
                "BOXIE_TEMPORAL_CA_FILE",
                "BOXIE_API_TEMPORAL_CERT_FILE",
                "BOXIE_API_TEMPORAL_KEY_FILE",
                "BOXIE_ORCHESTRATOR_TEMPORAL_CERT_FILE",
                "BOXIE_ORCHESTRATOR_TEMPORAL_KEY_FILE",
                "TEMPORAL_CERTS_DIR",
                "TEMPORAL_OPERATOR_CERTS_DIR",
                "TEMPORAL_UI_CERTS_DIR",
            )
        }
    )
    env.update(
        CLIS_RELEASE_ID="b" * 64,
        CLIS_RELEASE_REVISION="a" * 40,
        BOXIE_DOCKER_GID="999",
        OBS_METRICS_NETWORK="test-metrics",
        OBS_VECTOR_VOLUME="host-vector",
        OBS_LOGS_VOLUME="host-logs",
        OBS_PROMETHEUS_VOLUME="host-prometheus",
        OBS_GRAFANA_VOLUME="host-grafana",
        BOXIE_CONTROL_NETWORK="control",
        BOXIE_CONSUMER_NETWORK="consumer",
        BOXIE_RUNTIME_IMAGE="boxie:test",
        TEMPORAL_DB_PASSWORD="test",
        VISIBILITY_DB_PASSWORD="test",
        OBS_GRAFANA_HOSTNAME="grafana.example.com",
        OBS_GRAFANA_ADMIN_PASSWORD_FILE="/tmp/grafana-secret",
        OBS_GRAFANA_PLUGIN_DIR="/tmp/plugins",
        OBS_PROMETHEUS_CONFIG_FILE="/tmp/prometheus.yml",
        OBS_JOURNAL_GROUP_GID="190",
    )
    args = ["docker", "compose"]
    for file in files:
        args += ["-f", str(file)]
    result = subprocess.run(
        args + ["config", "--format", "json"],
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
        check=True,
    )
    return json.loads(result.stdout)  # type: ignore[no-any-return]


@pytest.mark.parametrize("manifest", ["compose.yml", "compose.production.yml"])
def test_base_monitoring_is_optional_and_selected_by_overlay(manifest: str) -> None:
    base = compose(INFRA / manifest)
    assert set(base["services"]) == {"api", "orchestrator"}
    assert set(base["networks"]) == {"consumer", "control"}
    for role in ("api", "orchestrator"):
        assert "BOXIE_METRICS_HOST" not in base["services"][role]["environment"]
    monitored = compose(INFRA / manifest, INFRA / "compose.observability.yml")
    for role in ("api", "orchestrator"):
        assert "metrics" in monitored["services"][role]["networks"]
        assert not monitored["services"][role].get("ports")


def test_monitoring_has_private_origins_independent_storage_and_bounded_defaults() -> None:
    config = compose(INFRA / "observability/compose.yml")
    assert len({volume["name"] for volume in config["volumes"].values()}) == 4
    for service in config["services"].values():
        assert not service.get("ports")
        assert not any("docker.sock" in mount["target"] for mount in service.get("volumes", []))
        assert int(service["mem_limit"]) > 0
    grafana = config["services"]["grafana"]
    assert int(grafana["mem_limit"]) == 268435456
    assert grafana["environment"]["GF_SERVER_ROOT_URL"] == "https://grafana.example.com"
    assert grafana["environment"]["GF_SECURITY_COOKIE_SECURE"] == "true"
    assert (
        "-retention.maxDiskSpaceUsageBytes=268435456"
        in config["services"]["victorialogs"]["command"]
    )
    assert all(not volume.get("external", False) for volume in config["volumes"].values())


def test_grafana_provisions_both_datasources_and_correlated_dashboard() -> None:
    root = INFRA / "observability/grafana"
    data = yaml.safe_load((root / "provisioning/datasources/operators.yml").read_text())
    assert {source["url"] for source in data["datasources"]} == {
        "http://prometheus:9090",
        "http://victorialogs:9428",
    }
    provider = yaml.safe_load((root / "provisioning/dashboards/operators.yml").read_text())
    assert provider["providers"][0]["options"]["path"] == "/etc/grafana/dashboards"
    dashboard = json.loads((root / "dashboards/boxie.json").read_text())
    assert {variable["name"] for variable in dashboard["templating"]["list"]} >= {
        "request_id",
        "sandbox_id",
    }
    assert {panel["datasource"]["uid"] for panel in dashboard["panels"]} == {
        "boxie-prometheus",
        "boxie-victorialogs",
    }


def test_temporal_monitoring_is_optional_and_origins_stay_private() -> None:
    base = compose(INFRA / "temporal/compose.yml")
    assert set(base["networks"]) == {"control"}
    monitored = compose(
        INFRA / "temporal/compose.yml", INFRA / "temporal/compose.observability.yml"
    )
    assert "metrics" in monitored["services"]["temporal"]["networks"]
    assert "operator-origin" in monitored["services"]["ui"]["networks"]
    assert all(not service.get("ports") for service in monitored["services"].values())


def test_operator_routes_require_distinct_access_audiences() -> None:
    from string import Template

    template = (INFRA / "ingress/cloudflared.yml").read_text()
    config = yaml.safe_load(
        Template(template).substitute(
            OPERATOR_TUNNEL_ID="00000000-0000-0000-0000-000000000001",
            OPERATOR_TEAM_NAME="operators",
            OPERATOR_GRAFANA_HOSTNAME="grafana.example.com",
            OPERATOR_TEMPORAL_HOSTNAME="temporal.example.com",
            OPERATOR_GRAFANA_AUDIENCE="grafana-aud",
            OPERATOR_TEMPORAL_AUDIENCE="temporal-aud",
        )
    )
    assert config["credentials-file"] == "/run/secrets/tunnel.json"
    assert config["ingress"][-1] == {"service": "http_status:404"}
    assert len(config["ingress"]) == 3
    for route, hostname, service, audience in zip(
        config["ingress"][:2],
        ["grafana.example.com", "temporal.example.com"],
        ["http://grafana:3000", "http://temporal-ui:8080"],
        ["grafana-aud", "temporal-aud"],
        strict=True,
    ):
        assert route == {
            "hostname": hostname,
            "service": service,
            "originRequest": {
                "access": {"required": True, "teamName": "operators", "audTag": [audience]}
            },
        }


def test_production_storage_is_host_owned() -> None:
    from unittest.mock import patch

    handles = {
        "POSTGRES_PASSWORD": "test",
        "BOXIE_DB_PASSWORD": "test",
        "POSTGRES_VOLUME": "host-postgres",
        "OBS_VECTOR_VOLUME": "host-vector",
        "OBS_LOGS_VOLUME": "host-logs",
        "OBS_PROMETHEUS_VOLUME": "host-prometheus",
        "OBS_GRAFANA_VOLUME": "host-grafana",
    }
    with patch.dict(os.environ, handles):
        for unit in ("postgres", "observability"):
            resolved = compose(
                INFRA / unit / "compose.yml", INFRA / unit / "compose.production.yml"
            )
            assert all(v["external"] for v in resolved["volumes"].values())
