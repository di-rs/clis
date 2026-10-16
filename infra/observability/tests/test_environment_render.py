"""The environment's disk and optional product wiring reaches actual exporter rules."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


@pytest.mark.parametrize("product", [False, True])
def test_environment_settings_render_persistent_disk_and_selected_scrapes(
    tmp_path: Path, product: bool
) -> None:
    settings = {
        "environment": "production",
        "host": "single-host",
        "metrics_network": "clis-metrics",
        "grafana_origin_network": "clis-grafana-origin",
        "vector_volume": "clis-vector",
        "logs_volume": "clis-logs",
        "prometheus_volume": "clis-prometheus",
        "grafana_volume": "clis-grafana",
        "backing_mounts": ["/", "/srv/boxie"],
        "consumer_scrapes": [{"job": "playground", "address": "playground:9104"}]
        if product
        else [],
    }
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(settings))
    gid = tmp_path / "journal-gid"
    gid.write_text("123\n")
    output = tmp_path / "rendered"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "observability_infra.render",
            "--settings",
            str(path),
            "--assets",
            str(Path(__file__).parents[1]),
            "--collector-image",
            "ghcr.io/example/collector@sha256:" + "a" * 64,
            "--journal-gid-file",
            str(gid),
            "--grafana-url",
            "https://grafana.example.com",
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    compose = yaml.safe_load((output / "compose.yml").read_text())
    exporter = compose["services"]["node-exporter"]
    assert "--collector.filesystem.mount-points-include=^(/|/srv/boxie)$" in exporter["command"]
    rules = yaml.safe_load((output / "rules/boxie.yml").read_text())
    disk_rules = [r for r in rules["groups"][0]["rules"] if r["alert"] == "HostDiskSpaceLow"]
    assert "/srv/boxie" in disk_rules[0]["expr"]
    jobs = yaml.safe_load((output / "prometheus.yml").read_text())["scrape_configs"]
    assert ("playground" in {job["job_name"] for job in jobs}) is product
    assert "OBS_JOURNAL_GROUP_GID=123\n" in (output / "observability.env").read_text()
