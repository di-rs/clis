"""Final rendered collector/storage assembly and bounded native observations."""

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import resource
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml
from prometheus_client.parser import text_string_to_metric_families

from observability_infra.config import Budgets, ObservabilityConfig, render_config
from observability_infra.smoke_application import REPO
from observability_infra.smoke_resources import Resources
from observability_infra.smoke_runtime import Commands, check_privacy, evidence
from observability_infra.smoke_watchdog import journal_identity

SERVICES = ("vector", "victorialogs", "prometheus", "node-exporter")
QUERY_PORTS = {"vector": 9598, "victorialogs": 9428, "prometheus": 9090}


def target_health(document: dict[str, Any], expected: dict[str, str]) -> dict[str, bool]:
    """Only fixed, uniquely identified private targets can satisfy native readiness."""
    entries = document.get("data", {}).get("activeTargets")
    if not isinstance(entries, list) or len(entries) > 16 or not 1 <= len(expected) <= 16:
        raise ValueError("invalid required target inventory")
    health = dict.fromkeys(expected, False)
    seen: set[str] = set()
    for item in entries:
        job = item.get("labels", {}).get("job")
        if (
            job not in expected
            or job in seen
            or item.get("scrapeUrl") != f"http://{expected[job]}/metrics"
        ):
            raise ValueError("foreign or duplicate required target")
        seen.add(job)
        try:
            age = (datetime.now(UTC) - datetime.fromisoformat(item["lastScrape"])).total_seconds()
        except KeyError, TypeError, ValueError:
            age = -1
        health[job] = item.get("health") == "up" and 0 <= age < 45
    return health


class Telemetry:
    def __init__(self, root: Path, commands: Commands, stack: Any) -> None:
        self.root, self.commands, self.stack = root, commands, stack
        self.env = dict(stack.env)
        self.project = root.name + "-telemetry"
        self.rendered = root / "rendered"
        self.endpoints: dict[str, str] = {}
        if (root / "telemetry.json").exists():
            self.endpoints = json.loads((root / "telemetry.json").read_text())
        if self.rendered.exists():
            self.load_environment()
        # Required interpolation even when Grafana is not started.
        self.env.update(
            OBS_GRAFANA_PLUGIN_DIR=str(root / "unused-plugins"),
            OBS_GRAFANA_ADMIN_PASSWORD_FILE=str(root / "unused-password"),
        )

    def load_environment(self) -> None:
        for line in (self.rendered / "observability.env").read_text().splitlines():
            name, value = line.split("=", 1)
            self.env[name] = value

    def compose_args(self, *args: str) -> list[str]:
        return [
            "docker",
            "compose",
            "--project-name",
            self.project,
            "--file",
            str(self.rendered / "compose.yml"),
            "--file",
            str(self.root / "telemetry-overlay.yml"),
            *args,
        ]

    def compose(self, *args: str) -> str:
        return self.commands.run(self.compose_args(*args), env=self.env)

    def container(self, service: str) -> str:
        identifier = self.compose("ps", "--all", "--quiet", service)
        if not re.fullmatch("[a-f0-9]{64}", identifier):
            raise RuntimeError("exact owned container identity unavailable")
        item = json.loads(self.commands.run(["docker", "inspect", identifier], env=self.env))[0]
        if item["Config"]["Labels"].get("com.docker.compose.project") != self.project:
            raise RuntimeError("foreign telemetry container")
        return identifier

    def initialize_volume(self, name: str, uid: int, gid: int) -> None:
        item = json.loads(self.commands.run(["docker", "volume", "inspect", name], env=self.env))[0]
        target = self.root / "docker-data/volumes" / name / "_data"
        if (
            name not in {self.root.name + suffix for suffix in ("-logs", "-prometheus", "-grafana")}
            or item["Name"] != name
            or item.get("Labels", {}).get("io.boxie.test.owner") != self.root.name
            or item["Driver"] != "local"
            or item.get("Options")
            or item["Mountpoint"] != str(target)
            or not target.is_dir()
            or target.resolve() != target
        ):
            raise RuntimeError("foreign telemetry volume initialization refused")
        # Only this newly created volume root, never recursive or a bind source.
        os.chown(target, uid, gid)
        actual = target.stat()
        if (actual.st_uid, actual.st_gid) != (uid, gid):
            raise RuntimeError("telemetry volume ownership initialization failed")

    def endpoint(self, service: str, *, expected: str | None = None) -> str:
        # Native host queries the existing internal bridge; internal-only services
        # cannot publish ports. Verify both sides of the exact owned attachment.
        if service not in QUERY_PORTS:
            raise RuntimeError("unsupported telemetry query service")
        info = json.loads(
            self.commands.run(["docker", "info", "--format", "{{json .}}"], env=self.env)
        )
        if (
            info["DockerRootDir"] != str(self.root / "docker-data")
            or "io.boxie.test.owner=" + self.root.name not in info["Labels"]
        ):
            raise RuntimeError("foreign telemetry query daemon")
        identifier = self.container(service)
        if expected is not None and identifier != expected:
            raise RuntimeError("telemetry container changed during fault")
        item = json.loads(self.commands.run(["docker", "inspect", identifier], env=self.env))[0]
        name = self.env["OBS_METRICS_NETWORK"]
        network = json.loads(
            self.commands.run(["docker", "network", "inspect", name], env=self.env)
        )[0]
        attachment = item.get("NetworkSettings", {}).get("Networks", {}).get(name, {})
        labels = item.get("Config", {}).get("Labels", {})
        try:
            address = ipaddress.IPv4Address(attachment["IPAddress"])
            member = ipaddress.IPv4Interface(network["Containers"][identifier]["IPv4Address"])
        except ValueError, KeyError, TypeError:
            raise RuntimeError("missing telemetry network address") from None
        if (
            item["Id"] != identifier
            or labels.get("com.docker.compose.project") != self.project
            or labels.get("com.docker.compose.service") != service
            or item.get("State", {}).get("Running") is not True
            or network["Name"] != name
            or network["Id"] not in self.stack.shared_networks
            or network.get("Labels", {}).get("io.boxie.test.owner") != self.stack.name
            or network.get("Internal") is not True
            or network.get("Driver") != "bridge"
            or attachment.get("NetworkID") != network["Id"]
            or address != member.ip
            or not address.is_private
            or address.is_loopback
            or address.is_unspecified
        ):
            raise RuntimeError("foreign telemetry network attachment")
        return f"http://{address}:{QUERY_PORTS[service]}"

    def refresh_endpoint(self, service: str, *, expected: str | None = None) -> None:
        self.endpoints[service] = self.endpoint(service, expected=expected)
        evidence(self.root / "telemetry.json", self.endpoints)

    def vector_identity(self, expected: str | None = None) -> dict[str, Any]:
        identifier = self.container("vector")
        item = json.loads(self.commands.run(["docker", "inspect", identifier], env=self.env))[0]
        state = item["State"]
        labels = item["Config"]["Labels"]
        if (
            item["Id"] != identifier
            or (expected is not None and identifier != expected)
            or labels.get("com.docker.compose.project") != self.project
            or labels.get("com.docker.compose.service") != "vector"
            or type(item["RestartCount"]) is not int
            or item["RestartCount"] < 0
            or type(state["Pid"]) is not int
            or state["Pid"] < 0
            or type(state["ExitCode"]) is not int
            or any(type(state[key]) is not bool for key in ("Running", "Restarting", "OOMKilled"))
            or not isinstance(state["StartedAt"], str)
            or not re.fullmatch(r"[0-9T:.Z+-]{1,40}", state["StartedAt"])
        ):
            raise RuntimeError("invalid Vector fault lifecycle identity")
        return {
            "Id": identifier,
            "RestartCount": item["RestartCount"],
            **{
                key: state[key]
                for key in ("Pid", "StartedAt", "Running", "Restarting", "ExitCode", "OOMKilled")
            },
        }

    def diagnostic_args(
        self,
        service: str,
        *,
        expected: str | None = None,
        since: str | None = None,
    ) -> list[str]:
        identifier = self.container(service)
        if expected is not None and identifier != expected:
            raise RuntimeError("foreign diagnostic container")
        # Reuse exact daemon/project/service/internal-network ownership checks.
        self.endpoint(service, expected=identifier)
        journal_identity(self.root, self.commands)
        args = [
            "journalctl",
            "--namespace=" + self.root.name,
            "--no-pager",
            "--quiet",
            "--all",
            "--output=cat",
            "CONTAINER_ID_FULL=" + identifier,
        ]
        if since is None:
            return [*args, "--lines=100"]
        return [*args, "--follow", "--lines=all", "--since=" + since]

    def prepare(self, resources: Resources) -> None:
        image = json.loads(
            self.commands.run(["docker", "image", "inspect", "boxie-collector:smoke"], env=self.env)
        )[0]
        image_id = image["Id"]
        if image["Architecture"] != "amd64" or not re.fullmatch("sha256:[a-f0-9]{64}", image_id):
            raise RuntimeError("native final collector image required")
        config = ObservabilityConfig(
            assets_dir=REPO / "tools/boxie/infra/observability",
            environment="smoke",
            host=self.stack.host,
            installation_id=self.stack.installation,
            metrics_network=self.env["OBS_METRICS_NETWORK"],
            grafana_origin_network=self.env["OBS_GRAFANA_ORIGIN_NETWORK"],
            journal_group_gid=resources.state["journal_gid"],
            collector_image="registry.example.invalid/collector@sha256:" + "a" * 64,
            vector_volume=resources.owner + "-vector",
            logs_volume=resources.owner + "-logs",
            prometheus_volume=resources.owner + "-prometheus",
            grafana_volume=resources.owner + "-grafana",
            grafana_public_url="https://grafana.example.com",
            journal_directory=str(self.root / "journal"),
            backing_mounts=("/", str(self.root / "vector"), resources.state["journal_path"]),
            budgets=Budgets(vector_buffer_bytes=268435488),
        )
        render_config(config, self.rendered)
        self.load_environment()
        volumes = [
            config.vector_volume,
            config.logs_volume,
            config.prometheus_volume,
            config.grafana_volume,
        ]
        resources.state["volumes"] = volumes
        resources.save()
        existing = self.commands.run(
            ["docker", "volume", "ls", "--format", "{{.Name}}"], env=self.env
        ).splitlines()
        if set(volumes) & set(existing):
            raise RuntimeError("refusing pre-existing telemetry volume")
        for name in volumes:
            args = [
                "docker",
                "volume",
                "create",
                "--label",
                "io.boxie.test.owner=" + resources.owner,
            ]
            if name == config.vector_volume:
                args += [
                    "--driver",
                    "local",
                    "--opt",
                    "type=none",
                    "--opt",
                    "o=bind",
                    "--opt",
                    "device=" + str(self.root / "vector"),
                ]
            created = self.commands.run([*args, name], env=self.env)
            if created != name:
                raise RuntimeError("created telemetry volume identity differs")
            if name != config.vector_volume:
                uid, gid = (472, 0) if name == config.grafana_volume else (65534, 65534)
                self.initialize_volume(name, uid, gid)
        overlay = {"services": {"vector": {"image": image_id, "pull_policy": "never"}}}
        (self.root / "telemetry-overlay.yml").write_text(yaml.safe_dump(overlay))
        effective = json.loads(self.compose("config", "--format", "json"))
        vector = effective["services"]["vector"]
        if vector["image"] != image_id or vector["pull_policy"] != "never":
            raise RuntimeError("collector fixture override missing")
        self.compose("up", "--detach", *SERVICES)
        actual = json.loads(
            self.commands.run(["docker", "inspect", self.container("vector")], env=self.env)
        )[0]
        if actual["Image"] != image_id:
            raise RuntimeError("running collector differs from inspected build")
        storage_actual = json.loads(
            self.commands.run(["docker", "inspect", self.container("victorialogs")], env=self.env)
        )[0]
        if "-retentionPeriod=7d" not in storage_actual["Config"]["Cmd"]:
            raise RuntimeError("native VictoriaLogs age setting differs")
        version = self.compose(
            "exec", "-T", "vector", "/usr/bin/journalctl", "--version"
        ).splitlines()[0]
        match = re.search(r"systemd (\d+)", version)
        if not match or int(match[1]) < 258:
            raise RuntimeError("all-boots journal reader too old")
        # Actual final UID/GID reader must open owned binary files, not JSON fixtures.
        self.compose(
            "exec",
            "-T",
            "vector",
            "/usr/bin/journalctl",
            "--directory=/var/log/journal",
            "--no-pager",
            "--lines=1",
            "--output=json",
        )
        for service in QUERY_PORTS:
            self.refresh_endpoint(service)
        evidence(
            self.root / "collector-provenance.json",
            {
                "local_image_id": image_id,
                "running_image_id": actual["Image"],
                "architecture": "amd64",
                "reader": version,
                "writer": self.commands.run(["journalctl", "--version"]).splitlines()[0],
                "source_id": "journal",
                "current_boot_only": False,
                "published": False,
                "vector_buffer_bytes": 268435488,
                "production_buffer_saturation_proved": False,
                "input_sha256": {
                    str(path.relative_to(REPO)): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in sorted(
                        (REPO / "tools/boxie/infra/observability/collector").iterdir()
                    )
                },
            },
        )

    async def query(self, service: str, path: str, *, params: dict[str, str] | None = None) -> str:
        async with asyncio.timeout(
            self.commands.deadline.remaining(restoring=self.commands.restoring)
        ):
            async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
                response = await client.get(self.endpoints[service] + path, params=params)
                response.raise_for_status()
                if len(response.content) > 262144:
                    raise RuntimeError("query evidence overflow")
                return response.text

    async def wait_required_targets(self) -> dict[str, bool]:
        config = yaml.safe_load((self.rendered / "prometheus.yml").read_text())
        expected = {
            job["job_name"]: job["static_configs"][0]["targets"][0]
            for job in config["scrape_configs"]
        }
        while True:
            self.commands.deadline.remaining()
            document = json.loads(
                await self.query("prometheus", "/api/v1/targets", params={"state": "active"})
            )
            health = target_health(document, expected)
            if all(health.values()):
                return health
            await asyncio.sleep(0.2)

    async def metrics(self, service: str) -> dict[str, list[dict[str, Any]]]:
        text = await self.query(service, "/metrics")
        values: dict[str, list[dict[str, Any]]] = {}
        for family in text_string_to_metric_families(text):
            for sample in family.samples:
                if sample.name.startswith(("vector_buffer_", "vl_", "boxie_log_")):
                    values.setdefault(sample.name, []).append(
                        {"labels": sample.labels, "value": sample.value}
                    )
        return values

    async def logs(self, query: str) -> list[dict[str, Any]]:
        text = await self.query(
            "victorialogs", "/select/logsql/query", params={"query": query, "limit": "1000"}
        )
        return [json.loads(line) for line in text.splitlines() if line]

    async def wait_logs(self, query: str) -> list[dict[str, Any]]:
        while True:
            found = await self.logs(query)
            if found:
                return found
            await asyncio.sleep(0.2)

    async def inspect_privacy(self, sentinels: tuple[str, ...], sandbox: str) -> dict[str, int]:
        records = await self.logs('sandbox_id:"' + sandbox + '"')
        check_privacy("\n".join(json.dumps(item) for item in records), sentinels)
        # Raw binary-journal decode and collector/storage diagnostics are separate surfaces.
        raw = await asyncio.to_thread(
            self.commands.scan_privacy,
            [
                "journalctl",
                "--directory=" + str(self.root / "journal"),
                "--no-pager",
                "--output=json",
                "--lines=200",
            ],
            sentinels,
            max_records=200,
        )
        tails = {}
        for service in ("vector", "victorialogs"):
            args = await asyncio.to_thread(self.diagnostic_args, service)
            tail = await asyncio.to_thread(
                self.commands.scan_privacy,
                args,
                sentinels,
                max_records=100,
                env=self.env,
                strict_stderr=True,
            )
            if tail["records"] == 0:
                raise RuntimeError("owned diagnostic privacy source empty")
            tails[service] = tail["records"]
        return {
            "decoded_records": len(records),
            "raw_journal_lines": raw["records"],
            "raw_journal_bytes": raw["bytes"],
            "diagnostic_tail_per_service": 100,
            "vector_diagnostic_records": tails["vector"],
            "victorialogs_diagnostic_records": tails["victorialogs"],
        }

    def checkpoint(self) -> dict[str, Any]:
        path = self.root / "vector/journal/checkpoint.txt"
        value = path.read_bytes()
        if not value or len(value) > 8192:
            raise RuntimeError("bounded persisted journal checkpoint required")
        return {
            "path": "journal/checkpoint.txt",
            "sha256": hashlib.sha256(value).hexdigest(),
            "bytes": len(value),
            "inode": path.stat().st_ino,
        }

    def container_sample(self, identifier: str) -> dict[str, Any]:
        item = json.loads(self.commands.run(["docker", "inspect", identifier], env=self.env))[0]
        if not re.fullmatch("[a-f0-9]{64}", item["Id"]) or not item["Id"].startswith(identifier):
            raise RuntimeError("resource container identity differs")
        state = item["State"]
        result = {
            "id": identifier,
            "name": item["Name"],
            "driver": item["HostConfig"]["LogConfig"],
            "oom_killed": state["OOMKilled"],
        }
        if state["OOMKilled"]:
            raise RuntimeError("owned container OOM")
        try:
            cgroup = Path(f"/proc/{state['Pid']}/cgroup").read_text().split("0::", 1)[1].strip()
        except FileNotFoundError:
            labels = item["Config"]["Labels"]
            if (
                labels.get("com.docker.compose.project") != self.project
                or labels.get("com.docker.compose.service") != "vector"
            ):
                raise
            after = self.vector_identity(item["Id"])
            before = {
                "Id": item["Id"],
                "RestartCount": item["RestartCount"],
                **{key: state[key] for key in ("Pid", "StartedAt", "Running", "Restarting")},
            }
            if after["OOMKilled"]:
                raise RuntimeError("owned container OOM") from None
            if after["RestartCount"] < before["RestartCount"]:
                raise RuntimeError("resource restart counter regressed") from None
            if not after["Restarting"] and not any(before[key] != after[key] for key in before):
                raise
            result.update(memory=None, transition={"before": before, "after": after})
            return result
        base = Path("/sys/fs/cgroup") / cgroup.lstrip("/")
        result["memory"] = {
            name: (base / name).read_text().strip()
            for name in ("memory.current", "memory.max", "memory.events", "pids.current")
        }
        return result

    def resources(self) -> dict[str, Any]:
        ids = self.commands.run(["docker", "ps", "--quiet"], env=self.env).split()
        result: dict[str, Any] = {"containers": [], "filesystems": {}}
        for identifier in ids:
            result["containers"].append(self.container_sample(identifier))
        for path in (self.root / "journal", self.root / "vector", self.root):
            stat = os.statvfs(path)
            result["filesystems"][str(path)] = {
                "bytes": stat.f_blocks * stat.f_frsize,
                "free": stat.f_bavail * stat.f_frsize,
            }
        resources = Resources(self.root, self.commands)
        result["systemd"] = {}
        for unit in (resources.unit, resources.journal_unit):
            cgroup = resources.owned_cgroup(unit)
            resources.state.setdefault("systemd_cgroups", {})[unit] = cgroup
            resources.save()
            base = Path("/sys/fs/cgroup") / cgroup.lstrip("/")
            result["systemd"][unit] = {
                name: (base / name).read_text().strip()
                for name in ("memory.current", "memory.max", "memory.events")
            }
            if result["systemd"][unit]["memory.max"] == "max":
                raise RuntimeError("uncapped owned daemon/journal")
        result["driver"] = {
            "address_space_limit": resource.getrlimit(resource.RLIMIT_AS)[0],
            "max_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        }
        result["at"] = time.monotonic()
        return result
