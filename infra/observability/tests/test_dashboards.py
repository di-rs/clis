"""Test rendered operator assets and meaningful missing-data contracts."""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

OWNER = Path(__file__).parents[1]


def dashboards() -> list[dict[str, Any]]:
    result = []
    for name in ("boxie", "telemetry"):
        path = OWNER / "grafana/dashboards" / f"{name}.json"
        assert path.is_file(), f"missing {name} operator dashboard"
        result.append(json.loads(path.read_text()))
    return result


def test_stable_datasources_and_explicit_target_contracts() -> None:
    boards = dashboards()
    sources = yaml.safe_load((OWNER / "grafana/provisioning/datasources.yml").read_text())[
        "datasources"
    ]
    identities = {d["uid"]: d["type"] for d in sources}
    assert identities == {
        "boxie-prometheus": "prometheus",
        "boxie-victorialogs": "victoriametrics-logs-datasource",
    }
    assert all(d["access"] == "proxy" and not d["editable"] for d in sources)
    assert {b["uid"] for b in boards} == {"boxie-operations", "boxie-telemetry"}
    for board in boards:
        for panel in board["panels"]:
            if panel["type"] == "text":
                continue
            assert panel["datasource"]["type"] == identities[panel["datasource"]["uid"]]
            assert panel["fieldConfig"]["defaults"]["noValue"]
            for target in panel["targets"]:
                assert target["datasource"] == panel["datasource"]
                assert target["expr"]
                if target["datasource"]["type"] == "prometheus":
                    assert "or vector(0)" not in target["expr"]
                    assert "request_id" not in target["expr"] and "sandbox_id" not in target["expr"]
                    assert "timestamp(up" in target["expr"]
                    assert target["instant"] != target["range"]
                    assert "lastNotNull" not in json.dumps(panel)


def test_ids_are_bounded_observed_single_select_log_variables() -> None:
    board = dashboards()[0]
    assert {v["name"] for v in board["templating"]["list"]} == {"request_id", "sandbox_id"}
    for var in board["templating"]["list"]:
        assert var["type"] == "query" and var["multi"] is False and var["includeAll"] is False
        assert var["query"]["type"] == "fieldValue" and var["query"]["limit"] == 100
        assert var["datasource"]["uid"] == "boxie-victorialogs"
        assert var["current"]["value"] == ""


@pytest.mark.parametrize("consumer", [False, True])
def test_render_installs_owned_assets_and_configuration_specific_health(
    tmp_path: Path, consumer: bool
) -> None:
    from observability_infra.config import ObservabilityConfig, ScrapeEndpoint, render_config

    config = ObservabilityConfig(
        assets_dir=OWNER,
        environment="test",
        host="test",
        metrics_network="metrics",
        grafana_origin_network="grafana-front",
        journal_group_gid=987,
        collector_image="example.invalid/collector@sha256:" + "a" * 64,
        vector_volume="vector",
        logs_volume="logs",
        prometheus_volume="prom",
        grafana_volume="grafana",
        grafana_public_url="https://grafana.example.com",
        backing_mounts=("/", "/data"),
        consumer_scrapes=(ScrapeEndpoint("playground", "playground:9104"),) if consumer else (),
    )
    render_config(config, tmp_path / "out")
    telemetry = json.loads((tmp_path / "out/grafana/dashboards/telemetry.json").read_text())
    text = json.dumps(telemetry)
    assert ("playground" in text) is consumer
    assert "/data" in text
    for panel in telemetry["panels"]:
        _assert_neutral_unknown(panel)
        _assert_readable_stat(panel)
    assert all(job.job in text for job in config.endpoints.scrapes)
    compose = yaml.safe_load((tmp_path / "out/compose.yml").read_text())
    grafana = compose["services"]["grafana"]
    assert grafana["environment"]["GF_SERVER_ROOT_URL"] == "https://grafana.example.com"
    assert grafana["environment"]["GF_AUTH_DISABLE_LOGIN_FORM"] == "false"
    assert grafana["environment"]["GF_AUTH_ANONYMOUS_ENABLED"] == "false"
    assert "./grafana/dashboards:/etc/grafana/dashboards:ro" in grafana["volumes"]


def test_grafana_budget_supports_selected_backend_processes() -> None:
    compose = yaml.safe_load((OWNER / "compose.yml").read_text())
    assert compose["services"]["grafana"]["pids_limit"] == 256
    assert compose["services"]["grafana"]["environment"]["GOGC"] == "25"
    assert "GOMEMLIMIT" not in compose["services"]["grafana"]["environment"]
    assert "GF_PLUGINS_FORWARD_HOST_ENV_VARS" not in compose["services"]["grafana"]["environment"]
    assert all(
        service["pids_limit"] == 128
        for name, service in compose["services"].items()
        if name != "grafana"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://grafana.example.com",
        "https://*.example.com",
        "https://grafana.example.com/path",
        "https://user:password@example.com",
    ],
)
def test_public_grafana_url_requires_exact_https_origin(url: str) -> None:
    import dataclasses

    from test_configuration import make_config

    with pytest.raises(ValueError):
        dataclasses.replace(make_config(), grafana_public_url=url)


def test_only_required_datasource_backends_are_enabled() -> None:
    env = yaml.safe_load((OWNER / "compose.yml").read_text())["services"]["grafana"]["environment"]
    assert set(env["GF_PLUGINS_DISABLE_PLUGINS"].split(",")) == {
        "elasticsearch",
        "grafana-postgresql-datasource",
        "grafana-pyroscope-datasource",
        "influxdb",
        "jaeger",
        "loki",
        "mssql",
        "mysql",
        "opentsdb",
        "stackdriver",
        "tempo",
        "zipkin",
    }
    assert env["GF_PLUGINS_ALLOW_LOADING_UNSIGNED_PLUGINS"] == ""
    assert env["GF_PLUGINS_PREINSTALL_DISABLED"] == "true"


def test_provisioning_aggregates_have_adjacent_producer_health() -> None:
    panels = dashboards()[0]["panels"]
    index = next(i for i, panel in enumerate(panels) if panel["id"] == 9)
    health = panels[index + 1]
    assert health["title"] == "Provisioning producer scrapes: API / worker"
    assert len(health["targets"]) == 2
    assert all("vector(-1)" in target["expr"] for target in health["targets"])
    assert {target["legendFormat"] for target in health["targets"]} == {"API", "worker"}


def _assert_neutral_unknown(panel: dict[str, Any]) -> None:
    defaults = panel["fieldConfig"]["defaults"]
    for missing in ("null", "nan"):
        assert any(
            mapping["type"] == "special"
            and mapping["options"]["match"] == missing
            and mapping["options"]["result"] == {"text": defaults["noValue"], "color": "gray"}
            for mapping in defaults.get("mappings", [])
        ), (panel["title"], missing)


def test_missing_values_are_neutral_instead_of_healthy_green() -> None:
    for board in dashboards():
        for panel in board["panels"]:
            _assert_neutral_unknown(panel)


def _assert_readable_stat(panel: dict[str, Any]) -> None:
    if panel["type"] != "stat":
        return
    defaults = panel["fieldConfig"]["defaults"]
    if defaults.get("unit") != "percent":
        assert defaults["color"] == {"mode": "fixed", "fixedColor": "gray"}
    assert all("__name__" not in target["legendFormat"] for target in panel["targets"])
    if len(panel["targets"]) > 1:
        assert panel["options"]["textMode"] == "value_and_name"


def test_raw_stats_do_not_use_grafana_default_failure_threshold() -> None:
    for board in dashboards():
        for panel in board["panels"]:
            _assert_readable_stat(panel)
    vector = next(p for p in dashboards()[1]["panels"] if p["id"] == 105)
    assert [t["legendFormat"] for t in vector["targets"]] == [
        "Actual buffer occupancy",
        "Configured buffer maximum",
    ]
    for board in dashboards():
        for panel in board["panels"]:
            if panel["fieldConfig"]["defaults"].get("unit") == "percent":
                assert panel["fieldConfig"]["defaults"]["color"]["mode"] == "thresholds"
