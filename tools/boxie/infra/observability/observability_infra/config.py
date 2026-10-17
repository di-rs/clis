"""Validate explicit deployment inputs and render owned nonsecret configuration only."""

import json
import re
from dataclasses import dataclass, fields
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID

import yaml

from observability_infra.dashboards import render_dashboards

_MIB = 1024**2
_GIB = 1024**3
_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}\Z")
_IMAGE = re.compile(r"[a-z0-9][a-z0-9./:_-]+@sha256:[a-f0-9]{64}\Z")
_ASSETS = (
    "compose.yml",
    "vector.yaml",
    "journald.conf",
    "event.schema.json",
    "versions.json",
    "prometheus.yml",
    "rules/boxie.yml",
    "grafana/provisioning/datasources.yml",
    "grafana/provisioning/dashboards.yml",
    "grafana/dashboards/boxie.json",
    "grafana/dashboards/telemetry.json",
)


def disk_buffer_capacity(configured: int) -> int:
    """Pinned Vector 0.59 reserves one 128 MiB file from unread-record capacity."""
    if type(configured) is not int or configured < 268435488:
        raise ValueError("invalid configured Vector disk capacity")
    return configured - 134217728


@dataclass(frozen=True)
class Budgets:
    journal_system_bytes: int = _GIB
    journal_runtime_bytes: int = 128 * _MIB
    journal_keep_free_bytes: int = 4 * _GIB
    journal_retention_seconds: int = 2 * 86400
    journal_file_seconds: int = 3600
    vector_buffer_bytes: int = 536870912
    vector_batch_bytes: int = _MIB
    vector_batch_events: int = 64
    vector_flush_seconds: int = 1
    logs_retention_days: int = 7
    logs_disk_bytes: int = 256 * _MIB
    prometheus_retention_days: int = 7
    prometheus_retention_bytes: int = 2 * _GIB
    prometheus_allocation_bytes: int = 3 * _GIB
    vector_memory_bytes: int = 256 * _MIB
    logs_memory_bytes: int = _GIB
    prometheus_memory_bytes: int = 512 * _MIB
    grafana_memory_bytes: int = 256 * _MIB
    exporter_memory_bytes: int = 64 * _MIB
    tunnel_memory_bytes: int = 128 * _MIB

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if type(value) is not int or not 0 < value <= 2**63 - 1:
                raise ValueError(f"invalid budget: {field.name}")
        if self.grafana_memory_bytes > 256 * _MIB:
            raise ValueError("Grafana memory must not exceed 256 MiB")
        if self.vector_buffer_bytes < 268435488:
            raise ValueError("Vector disk buffer below supported minimum")
        if self.vector_batch_bytes > _MIB or self.vector_batch_events > 64:
            raise ValueError("HTTP batch exceeds one MiB wire ceiling")
        if self.prometheus_allocation_bytes < max(3 * _GIB, self.prometheus_retention_bytes):
            raise ValueError(
                "Prometheus requires at least three GiB allocation and retention headroom"
            )


@dataclass(frozen=True)
class ScrapeEndpoint:
    job: str
    address: str

    def __post_init__(self) -> None:
        if not _NAME.fullmatch(self.job):
            raise ValueError("invalid scrape job")
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}:[1-9][0-9]{0,4}", self.address):
            raise ValueError("scrape address must be a private service alias and port")
        if int(self.address.rsplit(":", 1)[1]) > 65535:
            raise ValueError("invalid scrape port")


@dataclass(frozen=True)
class PrivateEndpoints:
    logs: str
    log_ingest: str
    prometheus: str
    grafana: str
    scrapes: tuple[ScrapeEndpoint, ...]


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
    journal_directory: str = "/var/log/journal"
    machine_id_path: str = "/etc/machine-id"
    backing_mounts: tuple[str, ...] = ("/",)
    exporter_socket_masks: tuple[str, ...] = ("/run", "/var/run")
    budgets: Budgets = Budgets()

    def __post_init__(self) -> None:
        for field in (
            "temporal_namespace",
            "environment",
            "host",
            "metrics_network",
            "grafana_origin_network",
            "vector_volume",
            "logs_volume",
            "prometheus_volume",
            "grafana_volume",
        ):
            if not _NAME.fullmatch(getattr(self, field)):
                raise ValueError(f"invalid literal: {field}")
        if not re.fullmatch(
            r"https://(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}",
            self.grafana_public_url,
        ):
            raise ValueError("explicit exact HTTPS Grafana public URL required")
        if self.metrics_network == self.grafana_origin_network:
            raise ValueError("network handles must be distinct")
        volumes = (
            self.vector_volume,
            self.logs_volume,
            self.prometheus_volume,
            self.grafana_volume,
        )
        if len(set(volumes)) != 4:
            raise ValueError("storage volumes must be independent")
        if type(self.journal_group_gid) is not int or not 0 < self.journal_group_gid < 2**31:
            raise ValueError("explicit nonroot host journal group required")
        if not _IMAGE.fullmatch(self.collector_image) or "timberio/vector" in self.collector_image:
            raise ValueError("published derived collector registry digest required")
        _path(self.journal_directory)
        if not self.journal_directory.endswith("/journal"):
            raise ValueError("mount only a persistent journal directory")
        _path(self.machine_id_path)
        if not self.machine_id_path.endswith("/machine-id"):
            raise ValueError("mount only machine identity")
        if not 1 <= len(self.backing_mounts) <= 16 or len(set(self.backing_mounts)) != len(
            self.backing_mounts
        ):
            raise ValueError("finite unique backing mounts required")
        for mount in self.backing_mounts:
            _path(mount, root=True)
        if not {"/run", "/var/run"} <= set(self.exporter_socket_masks):
            raise ValueError("standard socket locations must be masked")
        for mask in self.exporter_socket_masks:
            _path(mask)
            if mask in self.backing_mounts or mask == "/proc":
                raise ValueError("socket mask obscures a required mount")
        if str(UUID(self.installation_id)) != self.installation_id:
            raise ValueError("canonical Boxie installation UUID required")
        self.validate_assets()

    def validate_assets(self) -> None:
        for name in _ASSETS:
            if not (self.assets_dir / name).is_file():
                raise ValueError(f"missing owned configuration dependency: {name}")

    @property
    def _required_scrapes(self) -> tuple[ScrapeEndpoint, ...]:
        return tuple(
            ScrapeEndpoint(job, address)
            for job, address in (
                ("boxie-api", "boxie-api:9101"),
                ("boxie-orchestrator", "boxie-orchestrator:9103"),
                ("boxie-api-sdk", "boxie-api:9201"),
                ("boxie-orchestrator-sdk", "boxie-orchestrator:9203"),
                ("temporal", "temporal:9090"),
                ("vector", "vector:9598"),
                ("victorialogs", "victorialogs:9428"),
                ("node-exporter", "node-exporter:9100"),
                ("prometheus", "prometheus:9090"),
            )
        )

    @property
    def endpoints(self) -> PrivateEndpoints:
        logs = "http://victorialogs:9428"
        return PrivateEndpoints(
            logs,
            logs
            + "/insert/jsonline?_stream_fields=service,role,environment,host&_msg_field=message&_time_field=timestamp",
            "http://prometheus:9090",
            "http://grafana:3000",
            self._required_scrapes,
        )


def _path(value: str, *, root: bool = False) -> None:
    if (
        not re.fullmatch(r"/[a-zA-Z0-9_./-]*", value)
        or ".." in PurePosixPath(value).parts
        or str(PurePosixPath(value)) != value
        or (value == "/" and not root)
        or value.endswith(".sock")
    ):
        raise ValueError("unsafe host path")


def _yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text())


def _prometheus(config: ObservabilityConfig) -> tuple[str, str]:
    prometheus = _yaml(config.assets_dir / "prometheus.yml")
    templates = {job["job_name"]: job for job in prometheus["scrape_configs"]}
    jobs = []
    for endpoint in config.endpoints.scrapes:
        if endpoint.job not in templates:
            raise ValueError(f"unsupported scrape job: {endpoint.job}")
        job = templates[endpoint.job]
        job["static_configs"] = [{"targets": [endpoint.address]}]
        if endpoint.job.endswith("-sdk") or endpoint.job == "temporal":
            for rule in job["metric_relabel_configs"]:
                if rule.get("source_labels") == ["namespace"]:
                    rule["regex"] = re.escape(config.temporal_namespace) + (
                        "|_tag_excluded_|" if endpoint.job == "temporal" else "|"
                    )
                if rule.get("source_labels") in (["task_queue"], ["taskqueue"]):
                    prefix = re.escape(f"boxie/{config.installation_id}/{config.host}/")
                    rule["regex"] = (
                        prefix
                        + "(lifecycle|maintenance)"
                        + ("|_tag_excluded_|" if endpoint.job == "temporal" else "|")
                    )
        if endpoint.job == "node-exporter":
            job["metric_relabel_configs"][1]["regex"] = (
                "|".join(re.escape(m) for m in config.backing_mounts) + "|"
            )
        jobs.append(job)
    prometheus["scrape_configs"] = jobs
    rules = _yaml(config.assets_dir / "rules/boxie.yml")
    selected = {e.job for e in config.endpoints.scrapes}
    entries = rules["groups"][0]["rules"]
    entries[:] = [r for r in entries if r["alert"] != "RequiredScrapeUnavailable"]
    for job in sorted(selected):
        entries.append(
            {
                "alert": "RequiredScrapeUnavailable",
                "expr": f'up{{job="{job}"}} == 0 or absent(up{{job="{job}"}})',
                "for": "2m",
                "labels": {"severity": "warning", "job": job},
            }
        )
    mount_regex = "|".join(re.escape(m) for m in config.backing_mounts)
    for rule in entries:
        if rule["alert"] in ("HostDiskSpaceLow", "HostDiskSpaceCritical"):
            rule["expr"] = rule["expr"].replace(
                'mountpoint="/"', "mountpoint=~" + json.dumps(mount_regex)
            )
        if rule["alert"] == "HostDiskMetricsUnavailable":
            prefix = rule["expr"].split(" or absent(", 1)[0]
            rule["expr"] = prefix + "".join(
                ' or absent(node_filesystem_size_bytes{job="node-exporter",mountpoint='
                + json.dumps(m)
                + "})"
                for m in config.backing_mounts
            )
    return yaml.safe_dump(prometheus, sort_keys=False), yaml.safe_dump(rules, sort_keys=False)


def render_config(config: ObservabilityConfig, destination: Path) -> tuple[Path, ...]:
    """Write a new configuration directory, never provisioning or activating resources.

    assets_dir must explicitly identify the matching checkout/artifact assets even
    when this package is installed independently. No ambient environment is read.
    Grafana secret/plugin paths remain explicit release-owned mount references.
    """
    config.validate_assets()
    b = config.budgets
    versions = json.loads((config.assets_dir / "versions.json").read_text())
    vector = _yaml(config.assets_dir / "vector.yaml")
    vector["transforms"]["normalize"]["source"] = _normalizer(config)
    storage = vector["sinks"]["storage"]
    storage["uri"] = config.endpoints.log_ingest
    storage["buffer"][0]["max_size"] = b.vector_buffer_bytes
    storage["batch"] = {
        "max_bytes": b.vector_batch_bytes,
        "max_events": b.vector_batch_events,
        "timeout_secs": b.vector_flush_seconds,
    }
    compose = _yaml(config.assets_dir / "compose.yml")
    compose["services"]["grafana"]["environment"]["GF_SERVER_ROOT_URL"] = config.grafana_public_url
    compose["services"]["node-exporter"]["command"].append(
        "--collector.filesystem.mount-points-include=^("
        + "|".join(re.escape(m) for m in config.backing_mounts)
        + ")$"
    )
    compose["services"]["node-exporter"]["tmpfs"] = [
        f"/host{p}:ro,nosuid,nodev,noexec,mode=000,size=4096" for p in config.exporter_socket_masks
    ]
    env = {
        "OBS_COLLECTOR_IMAGE": config.collector_image,
        "OBS_METRICS_NETWORK": config.metrics_network,
        "OBS_GRAFANA_ORIGIN_NETWORK": config.grafana_origin_network,
        "OBS_VECTOR_VOLUME": config.vector_volume,
        "OBS_LOGS_VOLUME": config.logs_volume,
        "OBS_PROMETHEUS_VOLUME": config.prometheus_volume,
        "OBS_GRAFANA_VOLUME": config.grafana_volume,
        "OBS_JOURNAL_DIRECTORY": config.journal_directory,
        "OBS_MACHINE_ID_PATH": config.machine_id_path,
        "OBS_JOURNAL_GROUP_GID": str(config.journal_group_gid),
    }
    for key in ("victorialogs", "prometheus", "grafana", "node-exporter"):
        env["OBS_" + key.upper().replace("-", "_") + "_IMAGE"] = versions["images"][key]["image"]
    for field in fields(b):
        env["OBS_" + field.name.upper()] = str(getattr(b, field.name))
    journal = f"[Journal]\nStorage=persistent\nSystemMaxUse={b.journal_system_bytes}\nRuntimeMaxUse={b.journal_runtime_bytes}\nSystemKeepFree={b.journal_keep_free_bytes}\nMaxRetentionSec={b.journal_retention_seconds}s\nMaxFileSec={b.journal_file_seconds}s\n"
    prom, rules = _prometheus(config)
    contents = {
        "compose.yml": yaml.safe_dump(compose, sort_keys=False),
        "vector.yaml": yaml.safe_dump(vector, sort_keys=False),
        "journald.conf": journal,
        "observability.env": "\n".join(f"{key}={value}" for key, value in env.items()) + "\n",
        "prometheus.yml": prom,
        "rules/boxie.yml": rules,
    }
    contents.update(render_dashboards(config))
    # Build everything in memory before creating even the destination directory.
    destination.mkdir(parents=True, exist_ok=False)
    result = []
    for name, content in contents.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        result.append(path)
    return tuple(result)


def _normalizer(config: ObservabilityConfig) -> str:
    """Compile the owned JSON schema into fail-closed VRL checks (no raw errors)."""
    schema = json.loads((config.assets_dir / "event.schema.json").read_text())
    lines = [
        'tag = string(.CONTAINER_TAG) ?? ""',
        'service = "boxie"',
        'role = if tag == "boxie-api" { "api" } else { "orchestrator" }',
        'raw = string(.message) ?? ""',
        "event = {}",
        'valid = includes(["boxie-api", "boxie-orchestrator"], tag) && length(raw) <= 8192',
        "if valid { parsed, err = parse_json(raw); if err == null && is_object(parsed) { event = object!(parsed) } else { valid = false } }",
        "valid = valid && event.service == service && event.role == role",
    ]
    for name, prop in schema["properties"].items():
        value = f"event.{name}"
        checks = []
        if "const" in prop:
            checks.append(f"{value} == {json.dumps(prop['const'])}")
        if "enum" in prop:
            checks.append(f"includes({json.dumps(prop['enum'])}, {value})")
        kind = prop.get("type")
        if kind == "string":
            checks.append(f"is_string({value})")
            if "pattern" in prop:
                checks.append(f"match(string({value}) ?? \"\", r'{prop['pattern']}')")
            for bound, operator in (("minLength", ">="), ("maxLength", "<=")):
                if bound in prop:
                    checks.append(f'length(string({value}) ?? "") {operator} {prop[bound]}')
        if kind in ("integer", "number"):
            checks.append(
                f"is_integer({value})"
                if kind == "integer"
                else f"(is_integer({value}) || is_float({value}))"
            )
            checks.append(
                f"((to_float({value}) ?? -1.0) >= {prop['minimum']} && (to_float({value}) ?? -1.0) <= {prop['maximum']})"
            )
        expression = " && ".join(checks)
        if name not in schema["required"]:
            expression = f"(!exists({value}) || ({expression}))"
        lines.append(f"valid = valid && ({expression})")
    lines.extend(
        [
            f"valid = valid && length(keys(event)) <= {len(schema['properties'])}",
            "for_each(keys(event)) -> |_index, key| { if !includes("
            + json.dumps(list(schema["properties"]))
            + ", key) { valid = false } }",
            '_, timestamp_error = parse_timestamp(string(event.timestamp) ?? "", format: "%+")',
            "valid = valid && timestamp_error == null",
            # No event/journal content survives rejection, including parse error text.
            'if !valid { event = {"timestamp": format_timestamp!(now(), format: "%+"), "message": "event rejected", "event": "collector.rejected", "reason": "invalid", "service": service, "role": role} }',
            "event.environment = " + json.dumps(config.environment),
            "event.host = " + json.dumps(config.host),
            'if length(encode_json(event)) > 16383 { event = {"timestamp": format_timestamp!(now(), format: "%+"), "message": "event rejected", "event": "collector.rejected", "reason": "oversized", "service": service, "role": role, "environment": '
            + json.dumps(config.environment)
            + ', "host": '
            + json.dumps(config.host)
            + "} }",
            ". = event",
        ]
    )
    return "\n".join(lines) + "\n"
