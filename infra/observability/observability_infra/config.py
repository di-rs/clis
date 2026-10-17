"""Wire platform handles into the current native monitoring assets."""

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from string import Template
from uuid import UUID

import yaml


@dataclass(frozen=True)
class ScrapeEndpoint:
    job: str
    address: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", self.job):
            raise ValueError("invalid scrape job")
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}:[1-9][0-9]{0,4}", self.address):
            raise ValueError("private service alias and port required")
        if int(self.address.rsplit(":", 1)[1]) > 65535:
            raise ValueError("invalid scrape port")


@dataclass(frozen=True)
class ObservabilityConfig:
    assets_dir: Path
    environment: str
    host: str
    metrics_network: str
    grafana_origin_network: str
    journal_group_gid: int
    collector_image: str
    vector_volume: str
    logs_volume: str
    prometheus_volume: str
    grafana_volume: str
    grafana_public_url: str
    installation_id: str
    temporal_namespace: str = "boxie"
    backing_mounts: tuple[str, ...] = ("/",)
    consumer_scrapes: tuple[ScrapeEndpoint, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "environment",
            "host",
            "metrics_network",
            "grafana_origin_network",
            "vector_volume",
            "logs_volume",
            "prometheus_volume",
            "grafana_volume",
            "temporal_namespace",
        ):
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}", getattr(self, name)):
                raise ValueError("invalid deployment handle")
        if str(UUID(self.installation_id)) != self.installation_id:
            raise ValueError("canonical installation ID required")
        if not re.fullmatch(r"[a-z0-9][a-z0-9./:_-]+@sha256:[a-f0-9]{64}", self.collector_image):
            raise ValueError("immutable collector image required")
        if not re.fullmatch(r"https://(?:[a-z0-9-]+\.)+[a-z]{2,63}", self.grafana_public_url):
            raise ValueError("exact HTTPS Grafana origin required")
        if type(self.journal_group_gid) is not int or not 0 < self.journal_group_gid < 2**31:
            raise ValueError("nonroot journal group required")
        if self.metrics_network == self.grafana_origin_network:
            raise ValueError("distinct private networks required")
        if (
            len({self.vector_volume, self.logs_volume, self.prometheus_volume, self.grafana_volume})
            != 4
        ):
            raise ValueError("independent persistent volumes required")
        if not 1 <= len(self.backing_mounts) <= 16:
            raise ValueError("finite backing mount list required")
        for mount in self.backing_mounts:
            if (
                not re.fullmatch(r"/[a-zA-Z0-9_./-]*", mount)
                or ".." in PurePosixPath(mount).parts
                or str(PurePosixPath(mount)) != mount
            ):
                raise ValueError("absolute backing mount required")


def render_config(config: ObservabilityConfig, destination: Path) -> tuple[Path, ...]:
    """Copy current native assets and render only environment-owned wiring.

    Vector validation, dashboards, budgets and image pins stay with Boxie's
    native assets. This adapter neither provisions nor activates resources.
    """
    compose = yaml.safe_load((config.assets_dir / "compose.yml").read_text())
    prometheus = yaml.safe_load(
        Template((config.assets_dir / "prometheus.yml").read_text()).substitute(
            BOXIE_INSTALLATION_ID=config.installation_id,
            BOXIE_HOST_ID=config.host,
            BOXIE_TEMPORAL_NAMESPACE=config.temporal_namespace,
        )
    )
    rules = yaml.safe_load((config.assets_dir / "rules/boxie.yml").read_text())
    mount_regex = "|".join(re.escape(mount) for mount in config.backing_mounts)
    compose["services"]["node-exporter"]["command"].append(
        "--collector.filesystem.mount-points-include=^(" + mount_regex + ")$"
    )
    for job in prometheus["scrape_configs"]:
        if job["job_name"] == "node-exporter":
            job["metric_relabel_configs"][1]["regex"] = mount_regex + "|"
    jobs = {job["job_name"] for job in prometheus["scrape_configs"]}
    for endpoint in config.consumer_scrapes:
        if endpoint.job in jobs:
            raise ValueError("duplicate scrape job")
        jobs.add(endpoint.job)
        prometheus["scrape_configs"].append(
            {
                "job_name": endpoint.job,
                "static_configs": [{"targets": [endpoint.address]}],
            }
        )
        rules["groups"][0]["rules"].append(
            {
                "alert": "RequiredScrapeUnavailable",
                "expr": f'up{{job="{endpoint.job}"}} == 0 or absent(up{{job="{endpoint.job}"}})',
                "for": "2m",
                "labels": {"severity": "warning", "job": endpoint.job},
            }
        )
    for rule in rules["groups"][0]["rules"]:
        if rule["alert"] in {"HostDiskSpaceLow", "HostDiskSpaceCritical"}:
            rule["expr"] = rule["expr"].replace(
                'mountpoint="/"', "mountpoint=~" + json.dumps(mount_regex)
            )
        if rule["alert"] == "HostDiskMetricsUnavailable":
            rule["expr"] = rule["expr"].split(" or absent(", 1)[0] + "".join(
                ' or absent(node_filesystem_size_bytes{job="node-exporter",mountpoint='
                + json.dumps(mount)
                + "})"
                for mount in config.backing_mounts
            )
    for volume, handle in (
        ("vector-data", config.vector_volume),
        ("logs-data", config.logs_volume),
        ("prometheus-data", config.prometheus_volume),
        ("grafana-data", config.grafana_volume),
    ):
        compose["volumes"][volume] = {"external": True, "name": handle}
    compose["services"]["vector"]["image"] = config.collector_image
    compose["services"]["vector"].pop("build", None)
    compose["services"]["grafana"]["environment"]["GF_SERVER_ROOT_URL"] = config.grafana_public_url
    environment = {
        "BOXIE_ENVIRONMENT": config.environment,
        "BOXIE_HOST_ID": config.host,
        "BOXIE_INSTALLATION_ID": config.installation_id,
        "BOXIE_TEMPORAL_NAMESPACE": config.temporal_namespace,
        "OBS_METRICS_NETWORK": config.metrics_network,
        "OBS_GRAFANA_ORIGIN_NETWORK": config.grafana_origin_network,
        "OBS_JOURNAL_GROUP_GID": str(config.journal_group_gid),
    }
    # The copied native template documents the remaining private plugin/secret paths.
    contents = {
        "compose.yml": yaml.safe_dump(compose, sort_keys=False),
        "prometheus.yml": yaml.safe_dump(prometheus, sort_keys=False),
        "rules/boxie.yml": yaml.safe_dump(rules, sort_keys=False),
        "observability.env": "".join(f"{key}={value}\n" for key, value in environment.items()),
    }
    if destination.exists():
        raise FileExistsError(destination)
    shutil.copytree(config.assets_dir, destination)
    for name, content in contents.items():
        (destination / name).write_text(content)
    return tuple(path for path in destination.rglob("*") if path.is_file())
