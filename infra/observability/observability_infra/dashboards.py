"""Specialize owned dashboard assets to explicit targets and backing mounts."""

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from observability_infra.config import ObservabilityConfig

PROMETHEUS = {"type": "prometheus", "uid": "boxie-prometheus"}


def _health(job: str) -> str:
    selector = "{job=" + json.dumps(job) + "}"
    return f"((up{selector} == 1) and (time() - timestamp(up{selector}) < 45))"


def _guard(expr: str, job: str) -> str:
    return f"({expr}) and on(job,instance) {_health(job)}"


def _panel(
    identifier: int,
    title: str,
    expressions: list[str],
    description: str = "",
    unit: str = "short",
    series_labels: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "id": identifier,
        "title": title,
        "description": description,
        "type": "stat",
        "datasource": PROMETHEUS,
        "fieldConfig": {
            "defaults": {
                "noValue": "Unknown",
                "unit": unit,
                "color": {"mode": "fixed", "fixedColor": "gray"},
                "mappings": [
                    {
                        "type": "special",
                        "options": {
                            "match": missing,
                            "result": {"text": "Unknown", "color": "gray"},
                        },
                    }
                    for missing in ("null", "nan")
                ],
            },
            "overrides": [],
        },
        "options": {
            "reduceOptions": {"calcs": ["last"], "fields": "", "values": False},
            "graphMode": "none",
            "textMode": "value_and_name",
        },
        "targets": [
            {
                "refId": chr(65 + i),
                "datasource": PROMETHEUS,
                "editorMode": "code",
                "expr": expr,
                "instant": True,
                "range": False,
                "legendFormat": series_labels[i] if series_labels else title,
            }
            for i, expr in enumerate(expressions)
        ],
    }


def render_dashboards(config: ObservabilityConfig) -> dict[str, str]:
    """Return all assets before the renderer creates its destination."""
    root = config.assets_dir / "grafana"
    contents = {
        f"grafana/provisioning/{kind}/operators.yml": (
            root / "provisioning" / f"{kind}.yml"
        ).read_text()
        for kind in ("datasources", "dashboards")
    }
    boards = {
        name: json.loads((root / "dashboards" / f"{name}.json").read_text())
        for name in ("boxie", "telemetry")
    }
    panels = boards["telemetry"]["panels"]
    for i, endpoint in enumerate(config.endpoints.scrapes):
        selector = "{job=" + json.dumps(endpoint.job) + "}"
        p = _panel(
            200 + i,
            endpoint.job + ": scrape status",
            [f"(up{selector} and (time() - timestamp(up{selector}) < 45)) or on() vector(-1)"],
            "One configured target. Scrape success is not application readiness. Missing or older than 45s = Unknown; failed scrape = 0.",
        )
        p["fieldConfig"]["defaults"]["mappings"] += [
            {
                "type": "value",
                "options": {
                    "-1": {"text": "Unknown / missing", "color": "gray"},
                    "0": {"text": "Scrape failed", "color": "red"},
                    "1": {"text": "Scraping", "color": "green"},
                },
            }
        ]
        panels.append(p)
    for i, mount in enumerate(config.backing_mounts):
        selector = '{job="node-exporter",mountpoint=' + json.dumps(mount) + "}"
        matching = "on(job,instance,device,mountpoint,fstype)"
        size = "node_filesystem_size_bytes" + selector
        error = "node_filesystem_device_error" + selector
        collector = 'node_scrape_collector_success{job="node-exporter",collector="filesystem"}'
        ratio = f"100 * node_filesystem_avail_bytes{selector} / ({size} > 0) and {matching} ({error} == 0) and on(job,instance) ({collector} == 1)"
        panels.append(
            _panel(
                300 + i * 2,
                mount + ": filesystem available",
                [_guard(ratio, "node-exporter")],
                "Only the explicitly configured backing mount; missing/zero size or a filesystem error is Unknown, not 100% available.",
                "percent",
                ("{{mountpoint}} available on {{device}}",),
            )
        )
        panels[-1]["fieldConfig"]["defaults"].update(
            color={"mode": "thresholds"},
            thresholds={
                "mode": "absolute",
                "steps": [
                    {"color": "red", "value": None},
                    {"color": "orange", "value": 10},
                    {"color": "green", "value": 20},
                ],
            },
        )
        panels.append(
            _panel(
                301 + i * 2,
                mount + ": filesystem evidence",
                [
                    _guard(error, "node-exporter"),
                    _guard(collector, "node-exporter"),
                    _guard(f"({size} > bool 0)", "node-exporter"),
                ],
                "Device error 0=healthy; collector success 1=healthy; positive size 1=reported. Missing mount or failed scrape is Unknown.",
                series_labels=(
                    "Device error {{device}}",
                    "Collector success",
                    "Positive size {{device}}",
                ),
            )
        )
    for i, endpoint in enumerate(config.consumer_scrapes):
        # The scrape catalog validates supported consumer jobs before rendering.
        if endpoint.job != "playground":
            raise ValueError("no dashboard contract for optional consumer")
        selector = '{job="playground"}'
        expressions = [
            _guard(name + selector, endpoint.job)
            for name in (
                "playground_log_queue_occupancy",
                "playground_log_sink_available",
                "playground_log_dropped_total",
            )
        ]
        expressions.append(
            _guard(f"increase(playground_log_dropped_total{selector}[5m])", endpoint.job)
        )
        panels.append(
            _panel(
                400 + i,
                "Playground configured consumer telemetry",
                expressions,
                "Queue records, sink enabled 0/1, process lifetime drops and last 5m increase. Separate from Boxie and downstream counters.",
                series_labels=(
                    "Queue occupancy",
                    "Sink enabled",
                    "Lifetime drops",
                    "Drops in last 5m",
                ),
            )
        )
    for name, board in boards.items():
        for i, panel in enumerate(board["panels"]):
            panel["gridPos"] = {"x": (i % 2) * 12, "y": (i // 2) * 8, "w": 12, "h": 8}
        contents[f"grafana/dashboards/{name}.json"] = json.dumps(board, indent=2) + "\n"
    return contents
