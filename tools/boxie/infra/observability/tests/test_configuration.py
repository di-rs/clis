"""Exercise configuration boundaries before any daemon sees rendered files."""

import dataclasses
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
import yaml


def test_declared_renderer_is_available() -> None:
    assert importlib.util.find_spec("observability_infra") is not None, (
        "independent declared observability renderer is missing"
    )


def test_storage_manifest_has_no_published_ports_or_collector_socket() -> None:
    import yaml

    path = Path(__file__).parents[1] / "compose.yml"
    assert path.is_file(), "bounded storage manifest is missing"
    config = yaml.safe_load(path.read_text())
    for service in config["services"].values():
        assert not service.get("ports")
        assert service.get("network_mode") != "host"
    collector_mounts = str(config["services"]["vector"]["volumes"])
    assert "/var/run/docker.sock" not in collector_mounts
    assert len(config["volumes"]) == 4
    assert all(volume["external"] for volume in config["volumes"].values())


OWNER = Path(__file__).parents[1]


def make_config(**changes: Any) -> Any:
    from observability_infra.config import ObservabilityConfig

    changes.setdefault("assets_dir", OWNER)
    return ObservabilityConfig(
        installation_id="11111111-1111-4111-8111-111111111111",
        environment="test",
        host="fixture-host",
        metrics_network="fixture-metrics",
        grafana_origin_network="fixture-grafana",
        journal_group_gid=987,
        collector_image="registry.example.invalid/collector@sha256:" + "a" * 64,
        vector_volume="fixture-vector",
        logs_volume="fixture-logs",
        prometheus_volume="fixture-prometheus",
        grafana_volume="fixture-grafana-data",
        grafana_public_url="https://grafana.example.com",
        **changes,
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"backing_mounts": ()},
        {"backing_mounts": ("/", "/")},
        {"backing_mounts": ("/data/../",)},
        {"backing_mounts": ("/data.*",)},
        {"journal_directory": "/"},
        {"journal_directory": "/var/run/docker.sock"},
        {"machine_id_path": "/etc"},
        {"assets_dir": Path("/missing-assets")},
    ],
)
def test_invalid_inputs_fail_before_writing(changes: dict[str, Any], tmp_path: Path) -> None:
    from observability_infra.config import render_config

    with pytest.raises(ValueError):
        render_config(make_config(**changes), tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("vector_buffer_bytes", 1),
        ("vector_batch_bytes", 1048577),
        ("vector_batch_events", 65),
        ("vector_flush_seconds", 0),
        ("prometheus_allocation_bytes", 3 * 1024**3 - 1),
        ("journal_system_bytes", 0),
        ("journal_runtime_bytes", -1),
        ("logs_disk_bytes", 2**64),
        ("grafana_memory_bytes", 268435457),
        ("vector_memory_bytes", True),
    ],
)
def test_independent_budgets_reject_invalid_values(field: str, value: int) -> None:
    from observability_infra.config import Budgets

    with pytest.raises(ValueError):
        dataclasses.replace(Budgets(), **{field: value})


def test_no_shared_storage_or_network_and_no_mutable_collector() -> None:
    config = make_config()
    for change in (
        {"logs_volume": config.vector_volume},
        {"grafana_origin_network": config.metrics_network},
        {"collector_image": "collector:latest"},
        {"journal_group_gid": 0},
        {"environment": "bad\nSECRET=value"},
    ):
        with pytest.raises(ValueError):
            dataclasses.replace(config, **change)


def test_render_owns_only_nonsecret_config_and_exposes_private_endpoints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from observability_infra.config import render_config

    monkeypatch.setenv("SECRET_SENTINEL", "never-copy-this")
    config = make_config()
    paths = render_config(config, tmp_path / "rendered")
    assert isinstance(paths, tuple)
    assert {p.name for p in paths} >= {
        "compose.yml",
        "vector.yaml",
        "journald.conf",
        "prometheus.yml",
        "boxie.yml",
    }
    assert all(p.is_file() for p in paths)
    assert not any("never-copy-this" in p.read_text() for p in paths)
    assert config.endpoints.logs == "http://victorialogs:9428"
    assert config.endpoints.grafana == "http://grafana:3000"
    assert config.endpoints.prometheus == "http://prometheus:9090"
    rendered = yaml.safe_load((tmp_path / "rendered/vector.yaml").read_text())
    assert rendered["sources"]["journal"]["current_boot_only"] is False
    assert rendered["sources"]["journal"]["log_namespace"] is False
    assert rendered["sources"]["journal"]["batch_size"] == 16
    assert rendered["sinks"]["storage"]["buffer"] == [
        {"type": "disk", "max_size": 536870912, "when_full": "overflow"},
        {"type": "memory", "max_events": 1, "when_full": "drop_newest"},
    ]
    assert rendered["sinks"]["storage"]["acknowledgements"]["enabled"] is False
    assert rendered["sinks"]["storage"]["batch"]["max_events"] == 64
    assert "consumer_id" not in rendered["sinks"]["storage"]["uri"]
    with pytest.raises(FileExistsError):
        render_config(config, tmp_path / "rendered")


def test_boxie_targets_and_actual_mounts_drive_alerts(
    tmp_path: Path,
) -> None:
    from observability_infra.config import render_config

    config = make_config(backing_mounts=("/", "/data"))
    render_config(config, tmp_path / "out")
    prometheus = yaml.safe_load((tmp_path / "out/prometheus.yml").read_text())
    jobs = {j["job_name"]: j for j in prometheus["scrape_configs"]}
    assert "playground" not in jobs
    assert jobs["boxie-api-sdk"]["static_configs"][0]["targets"] == ["boxie-api:9201"]
    rules = yaml.safe_load((tmp_path / "out/rules/boxie.yml").read_text())["groups"][0]["rules"]
    required = {r["labels"]["job"] for r in rules if r["alert"] == "RequiredScrapeUnavailable"}
    assert required == set(jobs)
    absent = next(r["expr"] for r in rules if r["alert"] == "HostDiskMetricsUnavailable")
    assert 'mountpoint="/data"' in absent
    assert 'mountpoint="/"' in absent
    assert 'mountpoint=~"/|/data"' in next(
        r["expr"] for r in rules if r["alert"] == "HostDiskSpaceLow"
    )


def test_fixed_logging_and_private_service_networks() -> None:
    root = OWNER.parents[3]
    for manifest, tags in (
        (
            "tools/boxie/infra/compose.yml",
            {"api": "boxie-api", "orchestrator": "boxie-orchestrator"},
        ),
        (
            "tools/boxie/infra/compose.production.yml",
            {"api": "boxie-api", "orchestrator": "boxie-orchestrator"},
        ),
    ):
        services = yaml.safe_load((root / manifest).read_text())["services"]
        for name, tag in tags.items():
            assert services[name]["logging"] == {
                "driver": "journald",
                "options": {"mode": "non-blocking", "max-buffer-size": "4m", "tag": tag},
            }
            assert "metrics" in services[name]["networks"]
    services = yaml.safe_load((OWNER / "compose.yml").read_text())["services"]
    assert set(services["prometheus"]["networks"]) == {"metrics", "query"}
    assert set(services["grafana"]["networks"]) == {"query", "grafana-origin"}
    assert set(services["vector"]["networks"]) == {"metrics", "ingest"}
    assert services["vector"]["user"] == "65532:65532"
    assert services["vector"]["group_add"]
    assert services["vector"]["read_only"] is True


@pytest.mark.integration
def test_selected_vector_rejects_malformed_events_without_secret_enrichment(tmp_path: Path) -> None:
    """Execute the rendered VRL on journal-shaped envelopes, not binary journals."""
    import os
    import subprocess

    from observability_infra.config import render_config

    image = os.environ["OBS_TEST_COLLECTOR_IMAGE"]
    output = tmp_path / "rendered"
    render_config(make_config(), output)
    config = yaml.safe_load((output / "vector.yaml").read_text())
    event = {
        "schema_version": 1,
        "timestamp": "2026-10-16T00:00:00Z",
        "level": "INFO",
        "event": "request.completed",
        "message": "request completed",
        "service": "boxie",
        "role": "api",
        "release": "unknown",
        "instance_id": "00000000-0000-0000-0000-000000000000",
        "logger": "boxie",
        "request_id": "11111111-1111-1111-1111-111111111111",
    }
    cases: list[tuple[str, str, str, bool]] = [
        ("valid", json.dumps(event), "boxie-api", True),
        ("malformed", "SECRET_SENTINEL{", "boxie-api", False),
        ("array", '["SECRET_SENTINEL"]', "boxie-api", False),
        ("oversize", "SECRET_SENTINEL" * 1000, "boxie-api", False),
        ("forged_tag", json.dumps(event), "boxie-orchestrator", False),
    ]
    for name, changes in (
        ("unknown_field", {"secret": "SECRET_SENTINEL"}),
        ("timestamp", {"timestamp": "SECRET_SENTINEL"}),
        ("wrong_type", {"generation": "SECRET_SENTINEL"}),
        ("bad_enum", {"outcome": "SECRET_SENTINEL"}),
        ("invalid_uuid", {"request_id": "SECRET_SENTINEL"}),
        ("numeric_bound", {"duration_ms": 86400001}),
        ("message_bound", {"message": "SECRET_SENTINEL" * 20}),
        ("missing", {"schema_version": None}),
        ("enrichment", {"environment": "SECRET_SENTINEL"}),
    ):
        cases.append((name, json.dumps(event | changes), "boxie-api", False))
    config["tests"] = []
    for name, raw, tag, accepted in cases:
        condition = (
            '.event == "request.completed" && .request_id == "11111111-1111-1111-1111-111111111111"'
            if accepted
            else '.event == "collector.rejected" && !exists(.request_id)'
        )
        config["tests"].append(
            {
                "name": name,
                "inputs": [
                    {
                        "insert_at": "normalize",
                        "type": "log",
                        "log_fields": {
                            "message": raw,
                            "CONTAINER_TAG": tag,
                            "SECRET_ENV": "SECRET_SENTINEL",
                        },
                    }
                ],
                "outputs": [
                    {
                        "extract_from": "normalize",
                        "conditions": [
                            {
                                "type": "vrl",
                                "source": condition
                                + '\nassert!(.environment == "test")\nassert!(.host == "fixture-host")\nassert!(!contains(encode_json(.), "SECRET_SENTINEL"))',
                            }
                        ],
                    }
                ],
            }
        )
    # assert! for every condition; the last boolean alone would hide an earlier false.
    for test in config["tests"]:
        condition = test["outputs"][0]["conditions"][0]
        first, rest = condition["source"].split("\n", 1)
        condition["source"] = "assert!(" + first + ")\n" + rest
    (output / "vector-test.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--mount",
            f"type=bind,src={output},dst=/fixture,readonly",
            image,
            "test",
            "/fixture/vector-test.yaml",
        ],
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SECRET_SENTINEL" not in result.stderr


@pytest.mark.integration
def test_pinned_promtool_selected_profile_and_backing_mount_rules(
    tmp_path: Path,
) -> None:
    import subprocess

    from observability_infra.config import render_config

    versions = json.loads((OWNER / "versions.json").read_text())
    image = versions["images"]["prometheus"]["image"]
    for name, mounts in (("reviewed", ("/",)), ("mounts", ("/", "/data"))):
        output = tmp_path / name
        render_config(make_config(backing_mounts=mounts), output)
        fixture = yaml.safe_load(
            (
                OWNER
                / "tests/fixtures"
                / ("rules.yml" if name == "reviewed" else "mount-rules.yml")
            ).read_text()
        )
        fixture["rule_files"] = ["rules/boxie.yml"]
        (output / "rules-test.yml").write_text(yaml.safe_dump(fixture))
        for command in (
            ("check", "config", "/fixture/prometheus.yml"),
            ("test", "rules", "/fixture/rules-test.yml"),
        ):
            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--entrypoint",
                    "/bin/promtool",
                    "--mount",
                    f"type=bind,src={output},dst=/fixture,readonly",
                    image,
                    *command,
                ],
                text=True,
                capture_output=True,
                timeout=60,
            )
            assert result.returncode == 0, result.stdout + result.stderr


def relabel(labels: dict[str, str], rules: list[dict[str, Any]]) -> dict[str, str] | None:
    """Evaluate only the keep/labelkeep subset used by these owned scrape configs."""
    import re

    for rule in rules:
        regex = rule.get("regex", "(.*)")
        if rule["action"] == "keep":
            value = rule.get("separator", ";").join(
                labels.get(key, "") for key in rule["source_labels"]
            )
            if re.fullmatch(regex, value) is None:
                return None
        else:
            assert rule["action"] == "labelkeep"
            labels = {key: value for key, value in labels.items() if re.fullmatch(regex, key)}
    # Prometheus treats an absent label and an empty label as the same identity.
    return {key: value for key, value in labels.items() if value != ""}


@pytest.mark.parametrize(
    "job,expected", [("temporal", 109), ("node-exporter", 7), ("vector", 8), ("victorialogs", 18)]
)
def test_native_series_survive_final_filters_without_collisions(
    tmp_path: Path, job: str, expected: int
) -> None:
    from observability_infra.config import render_config

    render_config(make_config(backing_mounts=("/", "/etc/hosts")), tmp_path / "out")
    config = yaml.safe_load((tmp_path / "out/prometheus.yml").read_text())
    rules = next(
        j["metric_relabel_configs"] for j in config["scrape_configs"] if j["job_name"] == job
    )
    samples = json.loads((OWNER / f"tests/fixtures/{job}-native.json").read_text())["samples"]
    retained = []
    for sample in samples:
        if sample["labels"].get("taskqueue") == "boxie-lifecycle":
            sample["labels"]["taskqueue"] = (
                "boxie/11111111-1111-4111-8111-111111111111/fixture-host/lifecycle"
            )
        result = relabel(
            {"__name__": sample["name"], "job": job, "instance": "fixture", **sample["labels"]},
            rules,
        )
        if result is not None:
            retained.append(tuple(sorted(result.items())))
    assert len(retained) == expected
    assert len(set(retained)) == len(retained), "relabeling collapsed distinct native samples"


def test_native_unsafe_labels_and_unknown_mounts_are_filtered(tmp_path: Path) -> None:
    from observability_infra.config import render_config

    render_config(make_config(), tmp_path / "out")
    config = yaml.safe_load((tmp_path / "out/prometheus.yml").read_text())
    jobs = {
        j["job_name"]: j["metric_relabel_configs"]
        for j in config["scrape_configs"]
        if "metric_relabel_configs" in j
    }
    temporal = json.loads((OWNER / "tests/fixtures/temporal-native.json").read_text())["samples"][0]
    labels = {"__name__": temporal["name"], **temporal["labels"]}
    assert relabel(labels, jobs["temporal"]) is not None
    for key in (
        "partition",
        "forwarded",
        "task_priority",
        "worker_build_id",
        "worker_deployment_name",
        "worker_version",
        "operation",
        "source",
        "namespace_state",
        "otel_scope_name",
        "otel_scope_version",
        "task_type",
        "service_name",
    ):
        assert relabel(labels | {key: "SECRET_SENTINEL"}, jobs["temporal"]) is None
    filtered = relabel(labels | {"unknown": "SECRET_SENTINEL"}, jobs["temporal"])
    assert filtered is not None and "SECRET_SENTINEL" not in str(filtered)
    assert (
        relabel(
            {"__name__": "node_filesystem_size_bytes", "mountpoint": "/unknown"},
            jobs["node-exporter"],
        )
        is None
    )
    assert (
        relabel(
            {
                "__name__": "node_filesystem_size_bytes",
                "mountpoint": "/",
                "device_error": "SECRET_SENTINEL",
            },
            jobs["node-exporter"],
        )
        is None
    )
    assert (
        relabel(
            {"__name__": "node_scrape_collector_success", "collector": "SECRET_SENTINEL"},
            jobs["node-exporter"],
        )
        is None
    )


def test_source_backed_no_poller_scope_does_not_require_worker_version() -> None:
    # v1.32.0 pm.metricsHandler, unlike versioned physical-queue/backlog scopes,
    # is constructed by loggerAndMetricsForPartition without a worker version.
    config = yaml.safe_load((OWNER / "prometheus.yml").read_text())
    rules = next(
        j["metric_relabel_configs"] for j in config["scrape_configs"] if j["job_name"] == "temporal"
    )
    labels = {
        "__name__": "no_poller_tasks",
        "namespace": "boxie",
        "taskqueue": "boxie-lifecycle",
        "task_type": "Workflow",
        "service_name": "matching",
        "partition": "0",
        "namespace_state": "active",
        "operation": "TaskQueuePartitionManager",
        "otel_scope_name": "temporal",
    }
    assert relabel(labels, rules) is not None
    assert relabel(labels | {"worker_version": "SECRET_SENTINEL"}, rules) is None


def test_prometheus_binary_gigabyte_retention_renders_exact_bytes(tmp_path: Path) -> None:
    from observability_infra.config import render_config

    render_config(make_config(), tmp_path / "out")
    values = dict(
        line.split("=", 1) for line in (tmp_path / "out/observability.env").read_text().splitlines()
    )
    # Selected v3.15.0 --help: retention GB uses powers of two (1KB = 1024B).
    assert values["OBS_PROMETHEUS_RETENTION_BYTES"] == "2147483648"
    assert values["OBS_PROMETHEUS_ALLOCATION_BYTES"] == "3221225472"


def test_user_budget_defaults_reach_rendered_limits(tmp_path: Path) -> None:
    from observability_infra.config import Budgets, render_config

    budgets = Budgets()
    assert budgets.logs_disk_bytes == 268435456
    assert budgets.logs_retention_days == 7
    assert budgets.grafana_memory_bytes == 268435456
    assert dataclasses.replace(budgets, grafana_memory_bytes=268435455)
    render_config(make_config(), tmp_path / "out")
    env = dict(
        line.split("=", 1) for line in (tmp_path / "out/observability.env").read_text().splitlines()
    )
    assert env["OBS_LOGS_DISK_BYTES"] == "268435456"
    assert env["OBS_LOGS_RETENTION_DAYS"] == "7"
    assert env["OBS_GRAFANA_MEMORY_BYTES"] == "268435456"
    services = yaml.safe_load((tmp_path / "out/compose.yml").read_text())["services"]
    assert (
        "-retention.maxDiskSpaceUsageBytes=${OBS_LOGS_DISK_BYTES}"
        in services["victorialogs"]["command"]
    )
    assert "-retentionPeriod=${OBS_LOGS_RETENTION_DAYS}d" in services["victorialogs"]["command"]
    assert services["grafana"]["mem_limit"] == "${OBS_GRAFANA_MEMORY_BYTES:?Set memory cap}"
    for example in (
        OWNER / "observability.env.example",
        OWNER / "tests/fixtures/observability.env",
    ):
        assert "OBS_LOGS_DISK_BYTES=268435456" in example.read_text()
        assert "OBS_LOGS_RETENTION_DAYS=7" in example.read_text()
        assert "OBS_GRAFANA_MEMORY_BYTES=268435456" in example.read_text()


@pytest.mark.parametrize("configured,expected", [(268435488, 134217760), (536870912, 402653184)])
def test_disk_unread_capacity_reserves_one_pinned_data_file(configured: int, expected: int) -> None:
    from observability_infra.config import disk_buffer_capacity

    assert disk_buffer_capacity(configured) == expected


@pytest.mark.parametrize("configured", [0, 134217728, 268435487, float("nan"), None])
def test_invalid_disk_capacity_is_not_zero(configured: Any) -> None:
    from observability_infra.config import disk_buffer_capacity

    with pytest.raises(ValueError):
        disk_buffer_capacity(configured)


def test_exact_memory_tail_and_disk_metrics_do_not_collide(tmp_path: Path) -> None:
    from observability_infra.config import render_config

    render_config(make_config(), tmp_path / "out")
    config = yaml.safe_load((tmp_path / "out/prometheus.yml").read_text())
    rules = next(
        j["metric_relabel_configs"] for j in config["scrape_configs"] if j["job_name"] == "vector"
    )
    base = {
        "__name__": "vector_buffer_discarded_events_total",
        "component_id": "storage",
        "buffer_id": "storage",
        "component_kind": "sink",
        "component_type": "http",
        "intentional": "true",
    }
    disk = relabel(base | {"buffer_type": "disk", "stage": "0"}, rules)
    tail = relabel(base | {"buffer_type": "disk", "stage": "1"}, rules)
    assert disk is not None and tail is not None and disk != tail
    for kind, stage in [("disk", "2"), ("memory", "0"), ("memory", "1")]:
        assert relabel(base | {"buffer_type": kind, "stage": stage}, rules) is None
    assert (
        relabel(base | {"buffer_type": "disk", "stage": "1", "component_id": "foreign"}, rules)
        is None
    )
    fixture = json.loads((OWNER / "tests/fixtures/vector-chain-native.json").read_text())
    retained = []
    for sample in fixture["samples"]:
        labels = relabel({"__name__": sample["name"], **sample["labels"]}, rules)
        if sample["name"] == "vector_buffer_max_size_events":
            assert labels is None  # Diagnostic probe family is outside the scrape allowlist.
            continue
        assert labels is not None
        retained.append(json.dumps(labels, sort_keys=True))
    assert len(retained) == len(set(retained))


def test_render_targets_only_current_boxie_processes(tmp_path: Path) -> None:
    from observability_infra.config import render_config

    render_config(make_config(), tmp_path / "current")
    config = yaml.safe_load((tmp_path / "current/prometheus.yml").read_text())
    targets = {
        job["job_name"]: job["static_configs"][0]["targets"] for job in config["scrape_configs"]
    }
    assert targets["boxie-orchestrator"] == ["boxie-orchestrator:9103"]
    assert targets["boxie-orchestrator-sdk"] == ["boxie-orchestrator:9203"]
    assert not {"boxie-agent", "boxie-worker", "boxie-worker-sdk", "playground"} & targets.keys()
    vector = yaml.safe_load((tmp_path / "current/vector.yaml").read_text())
    assert vector["sources"]["journal"]["include_matches"]["CONTAINER_TAG"] == [
        "boxie-api",
        "boxie-orchestrator",
    ]


def test_rendered_sdk_scope_keeps_both_owned_queues_and_rejects_foreign_identity(
    tmp_path: Path,
) -> None:
    from observability_infra.config import render_config

    configured = dataclasses.replace(
        make_config(), host="sandbox.one", temporal_namespace="private"
    )
    render_config(configured, tmp_path / "scoped")
    document = yaml.safe_load((tmp_path / "scoped/prometheus.yml").read_text())
    rules = next(
        job["metric_relabel_configs"]
        for job in document["scrape_configs"]
        if job["job_name"] == "boxie-orchestrator-sdk"
    )
    prefix = f"boxie/{configured.installation_id}/sandbox.one/"
    labels = {
        "__name__": "temporal_activity_execution_failed_total",
        "namespace": "private",
        "activity_type": "maintain",
        "workflow_type": "Maintenance",
    }
    for queue in ("lifecycle", "maintenance"):
        assert relabel(labels | {"task_queue": prefix + queue}, rules) is not None
    for queue in (
        "boxie-lifecycle",
        prefix.replace("sandbox.one", "sandboxXone") + "lifecycle",
        prefix + "unowned",
    ):
        assert relabel(labels | {"task_queue": queue}, rules) is None
    assert (
        relabel(labels | {"namespace": "foreign", "task_queue": prefix + "maintenance"}, rules)
        is None
    )
    dashboard = (tmp_path / "scoped/grafana/dashboards/boxie.json").read_text()
    import re

    selectors = [
        target["expr"].split("task_queue=~", 1)[1]
        for panel in json.loads(dashboard)["panels"]
        for target in panel.get("targets", [])
        if "task_queue=~" in target.get("expr", "")
    ]
    assert selectors
    for selector in selectors:
        pattern, _ = json.JSONDecoder().raw_decode(selector)
        assert re.fullmatch(pattern, prefix + "maintenance")
        assert not re.fullmatch(
            pattern, prefix.replace("sandbox.one", "sandboxXone") + "maintenance"
        )


def test_collector_schema_accepts_current_termination_reasons() -> None:
    from boxie.domain.models import TerminationReason
    from boxie.observability.telemetry.context import validated

    allowed = json.loads((OWNER / "event.schema.json").read_text())["properties"]["reason"]["enum"]
    for reason in TerminationReason:
        produced = validated({"reason": reason.value})
        assert produced["reason"] in allowed
