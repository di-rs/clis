"""Nine concrete faults against one prepared native application/collector assembly."""

import asyncio
import errno
import json
import math
import os
import re
import signal
import stat
import sys
import time
from contextlib import AsyncExitStack
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from observability_infra.config import disk_buffer_capacity
from observability_infra.smoke_application import REPO, Application
from observability_infra.smoke_pipe_api import validate_shutdown
from observability_infra.smoke_retention import removal_observation, retained_archive
from observability_infra.smoke_runtime import (
    EventIdentityError,
    JournalRead,
    event_identities,
    evidence,
)
from observability_infra.smoke_samples import bounded, record_progress
from observability_infra.smoke_telemetry import Telemetry
from observability_infra.smoke_units import transient_command, unit_name
from observability_infra.smoke_watchdog import journal_identity, process_identity, restoration_valid

SCENARIOS = (
    "lifecycle-failure",
    "collector-restart",
    "storage-restart",
    "producer-pipe",
    "journal-stopped",
    "journal-retention",
    "journal-enospc",
    "vector-full",
    "vector-enospc",
)


def journal_usage(directory: Path) -> dict[str, int]:
    """Match journald's allocated-block accounting without following other sources."""
    result = {"files": 0, "apparent_bytes": 0, "allocated_bytes": 0}
    seen: set[tuple[int, int]] = set()
    with os.scandir(directory) as entries:
        for index, entry in enumerate(entries):
            if index >= 128:
                raise RuntimeError("journal usage inventory exceeded bound")
            if not entry.name.endswith((".journal", ".journal~")):
                continue
            try:
                info = entry.stat(follow_symlinks=False)
            except FileNotFoundError:  # Concurrent journal rotation/vacuum.
                continue
            if not stat.S_ISREG(info.st_mode):
                raise RuntimeError("unexpected journal usage source")
            identity = (info.st_dev, info.st_ino)
            if identity in seen:  # Rotation can expose the same inode under two names.
                continue
            seen.add(identity)
            result["files"] += 1
            result["apparent_bytes"] += info.st_size
            result["allocated_bytes"] += 512 * info.st_blocks
    return result


def total(metrics: dict[str, list[dict[str, Any]]], name: str) -> float:
    if name not in metrics:
        raise RuntimeError("required native metric unavailable: " + name)
    return sum(
        float(sample["value"])
        for sample in metrics[name]
        if not name.startswith("vector_") or sample["labels"].get("component_id") == "storage"
    )


def stage_metric(
    metrics: dict[str, list[dict[str, Any]]],
    name: str,
    kind: str,
    stage: str,
    *,
    intentional: str | None = None,
) -> float | None:
    selected = {
        "component_id": "storage",
        "buffer_id": "storage",
        "buffer_type": kind,
        "stage": stage,
    }
    if intentional is not None:
        selected["intentional"] = intentional
    samples = [
        sample
        for sample in metrics.get(name, [])
        if all(sample["labels"].get(key) == value for key, value in selected.items())
    ]
    if not samples:
        return None
    if len(samples) != 1:
        raise RuntimeError("ambiguous native buffer metric")
    value = float(samples[0]["value"])
    if not math.isfinite(value) or value < 0:
        raise RuntimeError("invalid native buffer metric")
    return value


def sequence_observation(emitted: int, accepted: set[str], delivered: list[str]) -> dict[str, Any]:
    if not 0 <= emitted <= 32:
        raise ValueError("bounded journal probe required")
    expected = {str(UUID(int=n)) for n in range(1, emitted + 1)}
    stored = set(delivered)
    return {
        "emitted": emitted,
        "accepted_identities": sorted(accepted & expected),
        "stored_identities": sorted(stored & expected),
        "stored_rows": len(delivered),
        "stored_duplicates": len(delivered) - len(stored),
        "emitted_not_observed_in_journal": len(expected - accepted),
        "emitted_not_observed_in_storage": len(expected - stored),
        "accepted_not_observed_in_storage": len((accepted & expected) - stored),
        "exactly_once": False,
        "interpretation": "bounded observation, not a final loss or complete delivery guarantee",
    }


class Scenario:
    def __init__(self, name: str, application: Application, telemetry: Telemetry) -> None:
        self.name, self.app, self.telemetry = name, application, telemetry
        self.root, self.commands = application.root, application.commands
        self.observations: dict[str, Any] = {"scenario": name, "synthetic_pressure": False}
        self.watch: str | None = None
        self.manifest = self.root / ("watch-" + name + ".json")
        self.flood_id: str | None = None
        self.flood_instance = str(uuid4())
        self.probe_instance = str(uuid4())

    async def command(self, *args: str) -> str:
        return await asyncio.to_thread(
            self.commands.run,
            list(args),
            env=dict(self.app.stack.env, LC_ALL="C"),
            empty_journal_ok=args[0] == "journalctl",
        )

    async def arm(self, data: dict[str, Any]) -> None:
        if self.watch is not None:
            raise RuntimeError("only one watchdog per scenario is permitted")
        self.watch = unit_name(self.root, self.name, watchdog=True)
        data["watchdog_unit"] = self.watch
        data["scenario"] = self.name
        data["restore_at"] = self.commands.deadline.end - self.commands.deadline.reserve + 2
        evidence(self.manifest, data)  # Register before any launch or fault.
        await asyncio.to_thread(
            self.commands.run,
            transient_command(
                self.root,
                self.name,
                [sys.executable, "-m", "observability_infra.smoke_watchdog", str(self.manifest)],
                watchdog=True,
            ),
        )
        ready = self.manifest.with_suffix(".unit.json")
        while not ready.exists():
            self.commands.deadline.remaining()
            await asyncio.sleep(0.05)
        identity = json.loads(ready.read_text())
        if identity["unit"] != self.watch or process_identity(identity["pid"]) != identity["start"]:
            raise RuntimeError("watchdog process identity differs")
        self.observations["watchdog"] = identity

    async def restore(self) -> None:
        if self.watch:
            self.manifest.with_suffix(".restore").touch()
            path = self.manifest.with_suffix(".result.json")
            async with asyncio.timeout(min(22, self.commands.deadline.remaining(restoring=True))):
                while not path.exists():
                    await asyncio.sleep(0.05)
            restored = json.loads(path.read_text())
            self.observations["restoration"] = restored
            if not restoration_valid(json.loads(self.manifest.read_text()), restored):
                raise RuntimeError("independent restoration incomplete")
            if restored.get("sink_started") is True:
                await asyncio.to_thread(self.telemetry.refresh_endpoint, "victorialogs")
        if self.flood_id:
            await self.command("docker", "rm", "--force", self.flood_id)
            self.flood_id = None
        if (
            self.name in ("journal-stopped", "journal-enospc")
            and "journal_during" in self.observations
        ):
            # Give the restored pipeline a bounded observation window, not a delivery promise.
            until = time.monotonic() + min(5, self.commands.deadline.remaining(restoring=True) / 3)
            while True:
                after = await self.journal_observation()
                if (
                    after["sequence"]["emitted_not_observed_in_storage"] == 0
                    or time.monotonic() >= until
                ):
                    break
                await asyncio.sleep(0.2)
            self.observations["journal_after"] = after
            before = self.observations["journal_before"]
            during = self.observations["journal_during"]
            self.observations["journal_storage_changes"] = {
                "runtime_growth_observed": during["runtime"]["bytes"] > before["runtime"]["bytes"],
                "persistent_files_removed_during": sorted(
                    set(before["persistent"]["inodes"]) - set(during["persistent"]["inodes"])
                ),
                "persistent_bytes_delta_during": during["persistent"]["bytes"]
                - before["persistent"]["bytes"],
                "runtime_is_collected": False,
            }
        if self.name == "journal-enospc" and self.watch:
            self.observations["journal_restored_identity"] = await asyncio.to_thread(
                journal_identity, self.root, self.commands
            )
            # Separate fresh delivery proof from the preceding fault-time observations.
            # A namespace restart does not flush its old runtime fallback entries.
            await self.journal_recovery_probe()
        self.app.stack.fault("fail_start", False)
        if self.name == "producer-pipe":
            for name in ("blocked-stdout", "stop-api"):
                (self.root / "control" / name).unlink(missing_ok=True)
            await asyncio.to_thread(
                self.app.stack.runtime_command, "up", "--detach", "--force-recreate", "api"
            )
            while True:
                try:
                    await self.app.request("GET", "/healthz")
                    break
                except Exception:
                    self.commands.deadline.remaining(restoring=True)
                    await asyncio.sleep(0.1)
        if self.name == "collector-restart":
            await asyncio.to_thread(self.telemetry.compose, "start", "vector")
            await asyncio.to_thread(self.telemetry.refresh_endpoint, "vector")
        if self.name in ("vector-full", "vector-enospc", "storage-restart"):
            while True:
                try:
                    after = await self.telemetry.metrics("vector")
                    if total(after, "vector_buffer_size_events") == 0:
                        self.observations["drained_buffer_events"] = 0
                        break
                except Exception:
                    self.commands.deadline.remaining(restoring=True)
                await asyncio.sleep(0.1)

    async def flood(self, limit: int, *, recovery: bool = False) -> None:
        self.observations["synthetic_pressure"] = True
        name = self.root.name + "-flood-" + self.name
        # Register exact unique name before creation; retain actual ID immediately.
        self.observations["flood_name"] = name
        if self.name == "vector-full":
            self.observations["pressure_pacing"] = {
                "maximum_events": limit,
                "maximum_events_per_second": 10000,
                "batch_events": 1000,
                "batch_wait_seconds": 0.1,
                "catch_up": False,
            }
        self.flood_id = await self.command(
            "docker",
            "run",
            "--detach",
            "--name",
            name,
            "--label",
            "io.boxie.test.owner=" + self.root.name,
            "--network",
            "none",
            "--memory",
            "64m",
            "--memory-swap",
            "64m",
            "--pids-limit",
            "32",
            "--log-driver",
            "journald",
            "--log-opt",
            "tag=boxie-api",
            "--log-opt",
            "mode=non-blocking",
            "--log-opt",
            "max-buffer-size=4m",
            "--mount",
            f"type=bind,src={REPO}/infra/observability/observability_infra/smoke_flood.py,dst=/flood.py,readonly",
            "--mount",
            f"type=bind,src={self.root}/control,dst=/control",
            "--entrypoint",
            "python",
            "boxie:lifecycle",
            "/flood.py",
            self.flood_instance,
            str(limit),
            *(["--paced"] if self.name == "vector-full" else []),
            *(
                [self.probe_instance]
                if self.name in ("journal-stopped", "journal-enospc") and not recovery
                else []
            ),
        )
        self.observations["flood_id"] = self.flood_id
        if self.name in ("journal-stopped", "journal-enospc") and not recovery:
            while True:
                path = self.root / "control/emitted.json"
                counter = json.loads(path.read_text()) if path.exists() else {}
                if (
                    counter.get("probe_instance") == self.probe_instance
                    and counter.get("probe_emitted") == 32
                ):
                    break
                self.commands.deadline.remaining()
                await asyncio.sleep(0.1)

    async def journal_recovery_probe(self) -> None:
        if self.flood_id is not None:
            raise RuntimeError("fault flood not removed before recovery probe")
        previous_instance = self.flood_instance
        previous: dict[str, Any] = {
            key: self.observations[key]
            for key in ("flood_id", "flood_name")
            if key in self.observations
        }
        self.flood_instance = str(uuid4())
        operation = str(UUID(int=1))
        proof: dict[str, Any] = {
            "instance_id": self.flood_instance,
            "operation_id": operation,
            "persistent_observed": False,
            "collector_observed": False,
            "old_runtime_entries_recovered": False,
        }
        self.observations["journal_delivery_recovery"] = proof
        try:
            await self.flood(1, recovery=True)
            proof["flood_id"] = self.flood_id
            while operation not in await self.accepted():
                await asyncio.sleep(0.1)
            proof["persistent_observed"] = True
            proof["persistent_read"] = self.observations.get("journal_acceptance_read")
            records = await self.telemetry.wait_logs(
                f'instance_id:"{self.flood_instance}" AND operation_id:"{operation}"'
            )
            if not any(item.get("operation_id") == operation for item in records):
                raise RuntimeError("fresh journal recovery marker missing from collector")
            proof["collector_observed"] = True
        finally:
            try:
                if self.flood_id is not None:
                    await self.command("docker", "rm", "--force", self.flood_id)
                    self.flood_id = None
            finally:
                self.flood_instance = previous_instance
                for key in ("flood_id", "flood_name"):
                    self.observations.pop(key, None)
                self.observations.update(previous)

    async def progress(self, sandbox: str) -> None:
        progress = await self.app.progress(sandbox)
        resources = await asyncio.to_thread(self.telemetry.resources)
        record_progress(self.observations, progress, resources)

    async def accepted(self) -> set[str]:
        # A small recovery batch is accepted only if present in real binary journal.
        read = await self.journal_read(self.root / "journal", self.flood_instance)
        self.observations["journal_acceptance_read"] = read.observation
        return self.journal_identities(read.text, self.flood_instance, "persistent")

    async def journal_read(self, directory: Path, instance: str) -> JournalRead:
        surface = "persistent" if directory == self.root / "journal" else "runtime"
        try:
            read = await asyncio.to_thread(
                self.commands.read_journal,
                [
                    "journalctl",
                    "--directory=" + str(directory),
                    "--no-pager",
                    "--output=cat",
                    "--grep=" + instance,
                    "--lines=100",
                ],
                directory=directory,
                allow_incomplete=self.name == "journal-enospc",
                env=dict(self.app.stack.env, LC_ALL="C"),
            )
        except Exception:
            if (
                self.commands.last_failure is not None
                and "journal_read" in self.commands.last_failure
            ):
                self.commands.last_failure["journal_read"]["surface"] = surface
            raise
        return JournalRead(read.text, dict(read.observation, surface=surface))

    def journal_identities(self, text: str, instance: str, surface: str) -> set[str]:
        if surface not in {"persistent", "runtime"}:
            raise ValueError("unknown journal observation surface")
        try:
            return event_identities(text, instance)
        except EventIdentityError as error:
            self.observations["journal_parse_failure"] = dict(
                error.detail, surface=surface, output_provenance="stdout_only"
            )
            raise

    async def recovered(self, accepted: set[str]) -> None:
        while True:
            records = await self.telemetry.logs('instance_id:"' + self.flood_instance + '"')
            delivered = {str(item["operation_id"]) for item in records}
            if accepted <= delivered:
                self.observations["delivery"] = {
                    "journal_accepted": len(accepted),
                    "stored_unique": len(delivered),
                    "stored_rows": len(records),
                    "duplicates": len(records) - len(delivered),
                    "missing_accepted": 0,
                }
                return
            await asyncio.sleep(0.2)

    async def recovery_case(self, sandbox: str) -> None:
        service = "vector" if self.name == "collector-restart" else "victorialogs"
        identifier = await asyncio.to_thread(self.telemetry.container, service)
        if service == "victorialogs":
            await self.arm({"sink": identifier})
        await self.command("docker", "stop", "--time=3", identifier)
        # A clean collector stop can flush its checkpoint; compare only the stopped interval.
        before_checkpoint = self.telemetry.checkpoint()
        await self.flood(20)
        while len(accepted := await self.accepted()) < 20:
            await asyncio.sleep(0.1)
        if (
            service == "vector"
            and self.telemetry.checkpoint()["sha256"] != before_checkpoint["sha256"]
        ):
            raise RuntimeError("checkpoint changed while collector stopped")
        await self.progress(sandbox)
        self.observations["checkpoint_before"] = before_checkpoint
        self.observations["before_restart"] = (
            await self.telemetry.metrics("vector")
            if service != "vector"
            else {"collector_stopped": True}
        )
        if service == "victorialogs":
            while total(await self.telemetry.metrics("vector"), "vector_buffer_size_events") == 0:
                await asyncio.sleep(0.1)
        await self.command("docker", "start", identifier)
        await asyncio.to_thread(self.telemetry.refresh_endpoint, service)
        await self.recovered(accepted)
        if service == "vector":
            # Restart same container/state twice: known accepted tail must remain searchable.
            await self.command("docker", "restart", "--time=3", identifier)
            await asyncio.to_thread(self.telemetry.refresh_endpoint, service)
            await self.recovered(accepted)
        self.observations["same_container_id"] = identifier
        self.observations["checkpoint_after"] = self.telemetry.checkpoint()

    async def wait_failed_metric(self, committed_at: float) -> None:
        # Require a positive value from a scrape after this operation began.
        query = (
            '(boxie_sandboxes{state="failed"} >= 1) and '
            f'(timestamp(boxie_sandboxes{{state="failed"}}) >= {committed_at})'
        )
        while True:
            metric = json.loads(
                await self.telemetry.query("prometheus", "/api/v1/query", params={"query": query})
            )
            if any(float(item["value"][1]) >= 1 for item in metric["data"]["result"]):
                return
            await asyncio.sleep(0.2)

    async def wait_rules(self) -> int:
        while True:
            self.commands.deadline.remaining()
            state: dict[str, Any] = {"groups": []}
            self.observations["rules_current"] = state
            response = json.loads(await self.telemetry.query("prometheus", "/api/v1/rules"))
            groups = response.get("data", {}).get("groups")
            if not isinstance(groups, list) or not 1 <= len(groups) <= 8:
                raise RuntimeError("native rule groups unavailable")
            pending = False
            unhealthy = False
            count = 0
            for group in groups:
                rules = group.get("rules") if isinstance(group, dict) else None
                if not isinstance(rules, list) or not 1 <= len(rules) <= 128:
                    raise RuntimeError("native rules unavailable")
                count += len(rules)
                if count > 128:
                    raise RuntimeError("native rule evidence bound exceeded")
                observed: list[dict[str, Any]] = []
                state["groups"].append(observed)
                for rule in rules:
                    if not isinstance(rule, dict):
                        raise RuntimeError("native rule malformed")
                    health = rule.get("health")
                    evaluation = rule.get("lastEvaluation")
                    if evaluation == "0001-01-01T00:00:00Z":
                        evaluated = "never"
                    else:
                        try:
                            if not isinstance(evaluation, str):
                                raise ValueError("missing evaluation timestamp")
                            timestamp = datetime.fromisoformat(evaluation)
                            evaluated = "completed" if timestamp.tzinfo is not None else "invalid"
                        except ValueError, TypeError:
                            evaluated = "invalid"
                    error_field = rule.get("lastError", "")
                    error = bool(error_field) or not isinstance(error_field, str)
                    observed.append(
                        {
                            "health": health if health in ("ok", "unknown", "err") else "invalid",
                            "evaluation": evaluated,
                            "error_present": error,
                        }
                    )
                    if health == "unknown" and evaluated == "never" and not error:
                        pending = True
                    elif health != "ok" or evaluated != "completed" or error:
                        unhealthy = True
            self.observations.setdefault("rules_initial", state)
            if unhealthy:
                raise RuntimeError("native rules unhealthy")
            if not pending:
                return len(groups)
            # Prometheus starts unknown until its first hash-slotted evaluation.
            await asyncio.sleep(min(0.2, self.commands.deadline.remaining()))

    async def terminal_privacy(self) -> None:
        sandbox = await self.app.provision()
        progress = await self.app.progress(sandbox)
        if progress.get("terminal_marker_observed") is not True:
            raise RuntimeError("terminal privacy marker was not exercised")
        await self.telemetry.wait_logs(f'sandbox_id:"{sandbox}" AND event:"terminal.detached"')
        inspected = await self.telemetry.inspect_privacy(self.app.sentinels, sandbox)
        self.observations["terminal_privacy"] = {
            "sandbox_id": sandbox,
            "exercised": True,
            "progress": progress,
            "surfaces": inspected,
        }

    async def failure_case(self) -> None:
        await self.terminal_privacy()
        committed_at = time.time()
        self.app.stack.fault("fail_start", True)
        sandbox = await self.app.provision(failed=True)
        marker = self.app.stack.root / "faults/failed_start_seen"
        identifier = marker.read_text() if marker.exists() else ""
        if not re.fullmatch("[a-f0-9]{64}", identifier):
            raise RuntimeError("failed-start owned container identity was not observed")
        remaining = await self.command(
            "docker", "ps", "--all", "--quiet", "--no-trunc", "--filter", "id=" + identifier
        )
        if remaining:
            raise RuntimeError("failed-start container was not physically removed")
        self.observations["failed_start_container_id"] = identifier
        self.observations["failed_start_container_removed"] = True
        committed = await asyncio.to_thread(self.app.committed, sandbox)
        if committed != {"state": "failed", "cleanup_pending": False}:
            raise RuntimeError("deliberate failure was not committed to SQL")
        self.observations["committed_sql"] = committed
        records = await self.telemetry.wait_logs(
            f'sandbox_id:"{sandbox}" AND event:"provisioning.completed" AND outcome:"failed"'
        )
        request_records = await self.telemetry.wait_logs(f'sandbox_id:"{sandbox}" AND request_id:*')
        request_ids = {
            str(UUID(item["request_id"])) for item in request_records if item.get("request_id")
        }
        if not request_ids:
            raise RuntimeError("server request correlation missing")
        for request in request_ids:
            await self.telemetry.wait_logs(f'request_id:"{request}"')
        await self.wait_failed_metric(committed_at)
        rule_groups = await self.wait_rules()
        logs_metrics = await self.telemetry.metrics("victorialogs")
        if total(logs_metrics, "vl_max_disk_space_usage_bytes") != 268435456:
            raise RuntimeError("native VictoriaLogs cleanup target differs")
        for item in records:
            stream = str(item.get("_stream", ""))
            if any(key in stream for key in ("sandbox_id", "request_id", "workflow_id")):
                raise RuntimeError("high cardinality stream dimension")
        self.observations.update(
            sandbox_id=sandbox,
            request_ids=sorted(request_ids),
            failed_records=len(records),
            rule_groups=rule_groups,
            logs_target_bytes=268435456,
            logs_max_age_days=7,
        )
        self.observations["privacy"] = await self.telemetry.inspect_privacy(
            self.app.sentinels, sandbox
        )

    async def journal_observation(self) -> dict[str, Any]:
        ownership = json.loads((self.root / "ownership.json").read_text())
        journal_name = Path(ownership["journal_path"]).name
        if not journal_name.endswith("." + self.root.name):
            raise RuntimeError("foreign runtime journal path")
        accepted: set[str] = set()
        observations: dict[str, Any] = {}
        for name, directory in (
            ("persistent", self.root / "journal"),
            ("runtime", Path("/run/log/journal") / journal_name),
        ):
            files = list(directory.glob("*.journal*"))
            if len(files) > 128:
                raise RuntimeError("journal observation file bound exceeded")
            stats = []
            for path in files:
                try:
                    stats.append(path.stat())
                except FileNotFoundError:
                    pass  # Rotation/deletion is itself part of this observation interval.
            identities: set[str] = set()
            read_observation: dict[str, Any] = {"complete": True, "warnings": {}}
            if files:
                read = await self.journal_read(directory, self.probe_instance)
                read_observation = read.observation
                identities = self.journal_identities(read.text, self.probe_instance, name)
                accepted |= identities
            observations[name] = {
                "read": read_observation,
                "files": len(files),
                "bytes": sum(item.st_size for item in stats),
                "inodes": sorted(item.st_ino for item in stats),
                "accepted_probe": sorted(identities),
                "free": os.statvfs(directory).f_bavail * os.statvfs(directory).f_frsize
                if directory.exists()
                else None,
            }
        counter = self.root / "control/emitted.json"
        emitted = json.loads(counter.read_text()) if counter.exists() else {}
        own = emitted.get("instance") == self.flood_instance
        count = int(emitted.get("probe_emitted", 0)) if own else 0
        records = await self.telemetry.logs(f'instance_id:"{self.probe_instance}"')
        observations["sequence"] = sequence_observation(
            count, accepted, [str(item["operation_id"]) for item in records]
        )
        complete = all(observations[name]["read"]["complete"] for name in ("persistent", "runtime"))
        observations["sequence"]["journal_read_complete"] = complete
        observations["sequence"]["accepted_identities_are_lower_bound"] = not complete
        if not complete:
            observations["sequence"]["emitted_not_observed_in_journal"] = None
        observations["pressure_emitted_lower_bound"] = int(emitted["emitted"]) if own else 0
        return observations

    async def journal_stopped(self, sandbox: str) -> None:
        self.observations["journal_before"] = await self.journal_observation()
        unit = f"systemd-journald@{self.root.name}.service"
        pid = int(await self.command("systemctl", "show", unit, "--property=MainPID", "--value"))
        identity = process_identity(pid)
        await self.arm({"pid": pid, "start": identity})
        os.kill(pid, signal.SIGSTOP)
        while Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "T":
            await asyncio.sleep(0.01)
        await self.flood(500000)
        await self.progress(sandbox)
        # Real provisioning while selected receiver remains stopped.
        extra = await self.app.provision()
        await self.progress(extra)
        if (
            process_identity(pid) != identity
            or Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "T"
        ):
            raise RuntimeError("journal fault ended before progress barrier")
        self.observations["journal_during"] = await self.journal_observation()
        self.observations["receiver_stopped_through_probes"] = True
        self.observations["docker_drop_counter"] = "not exported; no invented drop metric"

    def enospc_checkpoint(self, phase: str) -> None:
        if self.name not in ("journal-enospc", "vector-enospc"):
            return
        fault_phases = {
            "allocation_started",
            "allocation_progress",
            "allocation_completed",
            "flood_launch",
            "flood_started",
            "diagnostic_wait",
            "release_started",
            "restart_started",
            "restart_completed",
        }
        if phase not in fault_phases | {
            "exercise_failed",
            "restoration_started",
            "application_cleanup_started",
            "cleanup_completed",
        }:
            raise ValueError("unknown ENOSPC checkpoint")
        fault_phase = phase if phase in fault_phases else self.observations.get("enospc_phase")
        selected = {
            key: self.observations[key]
            for key in (
                "enospc_observed",
                "allocation",
                "progress",
                "vector_pressure",
                "vector_fault_state",
                "vector_fault_metrics",
                "vector_fault_diagnostics",
                "vector_enospc_diagnostic_observed",
                "exercise_errors",
                "checkpoint_cleanup_errors",
            )
            if key in self.observations
        }
        data = bounded(
            {
                "scenario": self.name,
                "status": "in_progress",
                "final": False,
                "phase": phase,
                "fault_phase": fault_phase,
                "elapsed_seconds": self.commands.deadline.elapsed,
                **selected,
            },
            16384,
        )
        evidence(self.root / ("enospc-checkpoint-" + self.name + ".json"), data)
        self.observations["enospc_phase"] = fault_phase

    async def exhaustion(self, sandbox: str) -> None:
        vector = self.name == "vector-enospc"
        if not vector:
            self.observations["journal_before"] = await self.journal_observation()
        directory = self.root / ("vector" if vector else "journal")
        filler = directory / ("filler-" + self.name)
        with filler.open("xb"):
            pass
        data: dict[str, Any] = {"fillers": [{"path": str(filler), "inode": filler.stat().st_ino}]}
        if not vector:
            data["journal_restart"] = await asyncio.to_thread(
                journal_identity, self.root, self.commands
            )
        if vector:
            data["sink"] = await asyncio.to_thread(self.telemetry.container, "victorialogs")
        await self.arm(data)
        if vector:
            await self.command("docker", "stop", "--time=3", data["sink"])
        self.enospc_checkpoint("allocation_started")
        # This operation fills only the independently mounted small ext4 filesystem.
        observed = False
        with filler.open("r+b", buffering=0) as stream:
            while True:
                self.commands.deadline.remaining()
                try:
                    if sys.platform != "linux":
                        raise RuntimeError("Linux allocation required")
                    os.posix_fallocate(stream.fileno(), stream.tell(), 1024 * 1024)
                    stream.seek(1024 * 1024, 1)
                    if stream.tell() % (64 * 1024**2) == 0:
                        self.observations["allocation"] = {
                            "filler_offset": stream.tell(),
                            "free_bytes": os.statvfs(directory).f_bavail
                            * os.statvfs(directory).f_frsize,
                        }
                        self.enospc_checkpoint("allocation_progress")
                except OSError as error:
                    if error.errno != errno.ENOSPC:
                        raise
                    try:
                        stream.write(b"x" * 4096)
                    except OSError as final:
                        if final.errno == errno.ENOSPC:
                            observed = True
                            break
        if not observed:
            raise RuntimeError("filesystem exhaustion precondition unavailable")
        self.observations["enospc_observed"] = True
        self.enospc_checkpoint("allocation_completed")
        self.enospc_checkpoint("flood_launch")
        await self.flood(500000)
        self.enospc_checkpoint("flood_started")
        await self.progress(sandbox)
        if vector:
            while True:
                self.enospc_checkpoint("diagnostic_wait")
                diagnostics = await asyncio.to_thread(
                    self.telemetry.compose, "logs", "--no-color", "--tail=100", "vector"
                )
                self.observations["vector_fault_diagnostics"] = {
                    "errno_text_observed": "No space left on device" in diagnostics
                    or "os error 28" in diagnostics,
                    "writer_unrecoverable_observed": "Disk buffer writer has encountered an unrecoverable error."
                    in diagnostics,
                }
                self.enospc_checkpoint("diagnostic_wait")
                identifier = await asyncio.to_thread(self.telemetry.container, "vector")
                info = json.loads(await self.command("docker", "inspect", identifier))[0]
                if info["Id"] != identifier:
                    raise RuntimeError("Vector fault container identity differs")
                self.observations["vector_fault_state"] = {
                    key: info["State"][key] for key in ("Running", "ExitCode", "OOMKilled")
                }
                self.enospc_checkpoint("diagnostic_wait")
                try:
                    metrics = await self.telemetry.metrics("vector")
                except Exception as error:
                    self.observations["vector_fault_metrics"] = {
                        "available": False,
                        "error_type": type(error).__name__,
                    }
                    self.commands.deadline.remaining()
                else:
                    self.observations["vector_fault_metrics"] = {"available": True}
                    try:
                        await self.buffer_pressure(metrics, None)
                    except Exception as error:
                        self.observations["vector_fault_metrics"].update(
                            observation_valid=False, error_type=type(error).__name__
                        )
                        self.enospc_checkpoint("diagnostic_wait")
                        raise
                self.enospc_checkpoint("diagnostic_wait")
                if "No space left on device" in diagnostics or "os error 28" in diagnostics:
                    self.observations["vector_enospc_diagnostic_observed"] = True
                    break
                await asyncio.sleep(0.5)
                await self.progress(sandbox)
        else:
            await asyncio.sleep(2)
            await self.progress(sandbox)
            self.observations["persistent_free_during_fault"] = (
                os.statvfs(directory).f_bavail * os.statvfs(directory).f_frsize
            )
        if not vector:
            self.observations["journal_during"] = await self.journal_observation()
        self.enospc_checkpoint("release_started")
        filler.unlink()
        if vector:
            self.enospc_checkpoint("restart_started")
            identifier = await asyncio.to_thread(self.telemetry.container, "vector")
            await self.command("docker", "restart", "--time=3", identifier)
            await self.command("docker", "start", data["sink"])
            for service in ("vector", "victorialogs"):
                await asyncio.to_thread(self.telemetry.refresh_endpoint, service)
            self.observations["same_state_restart_id"] = identifier
            self.enospc_checkpoint("restart_completed")
        self.observations["free_after_release"] = (
            os.statvfs(directory).f_bavail * os.statvfs(directory).f_frsize
        )

    async def buffer_pressure(
        self, metrics: dict[str, list[dict[str, Any]]], baseline_drops: float | None
    ) -> tuple[float, float | None]:
        occupancy = stage_metric(metrics, "vector_buffer_size_bytes", "disk", "0")
        maximum = stage_metric(metrics, "vector_buffer_max_size_bytes", "disk", "0")
        if occupancy is None or maximum is None or not maximum.is_integer():
            raise RuntimeError("native disk capacity observation missing")
        usable = disk_buffer_capacity(int(maximum))
        if maximum != 268435488:
            raise RuntimeError("native disk configured capacity differs")
        drops = stage_metric(
            metrics, "vector_buffer_discarded_events_total", "disk", "1", intentional="true"
        )
        delta = None if drops is None or baseline_drops is None else drops - baseline_drops
        info = json.loads(await self.command("docker", "inspect", str(self.flood_id)))[0]
        if (
            info["Id"] != self.flood_id
            or info["Name"] != "/" + self.root.name + "-flood-" + self.name
            or info["Config"]["Labels"].get("io.boxie.test.owner") != self.root.name
        ):
            raise RuntimeError("foreign pressure flood identity")
        state = info["State"]
        if (
            type(state["Running"]) is not bool
            or type(state["OOMKilled"]) is not bool
            or type(state["ExitCode"]) is not int
            or state["Status"]
            not in {"created", "running", "exited", "restarting", "dead", "paused"}
        ):
            raise RuntimeError("invalid pressure flood state")
        emitted: int | None = None
        path = self.root / "control/emitted.json"
        if path.exists():
            with path.open("rb") as stream:
                raw = stream.read(4097)
            if len(raw) > 4096:
                raise RuntimeError("pressure emission evidence exceeded bound")
            counter = json.loads(raw)
            if counter.get("instance") == self.flood_instance:
                value = counter["emitted"]
                if type(value) is not int or not 0 <= value <= 1000000:
                    raise RuntimeError("invalid pressure emission counter")
                emitted = value
        current = {
            "buffer_bytes": occupancy,
            "configured_disk_bytes": maximum,
            "usable_disk_bytes": usable,
            "disk_file_reserve_bytes": 134217728,
            "discarded_delta": delta,
            "discarded_counter_present": drops is not None,
            "memory_tail_intentional_discarded": drops,
            "disk_unintentional_discarded": stage_metric(
                metrics, "vector_buffer_discarded_events_total", "disk", "0", intentional="false"
            ),
            "emitted_lower_bound": emitted,
            "flood_state": {
                "running": state["Running"],
                "status": state["Status"],
                "exit_code": state["ExitCode"],
                "oom_killed": state["OOMKilled"],
            },
        }
        prior = self.observations.get("vector_pressure", {})
        self.observations["vector_pressure"] = {
            "samples": prior.get("samples", 0) + 1,
            "baseline_discarded": baseline_drops,
            "drop_metric_labels": {
                "component_id": "storage",
                "buffer_id": "storage",
                "buffer_type": "disk",
                "stage": "1",
                "intentional": "true",
            },
            "first": prior.get("first", current),
            "latest": current,
            "peak_buffer_bytes": max(occupancy, prior.get("peak_buffer_bytes", occupancy)),
            "peak_discarded_delta": max(
                value for value in (delta, prior.get("peak_discarded_delta")) if value is not None
            )
            if delta is not None or prior.get("peak_discarded_delta") is not None
            else None,
        }
        if state["OOMKilled"] or (not state["Running"] and state["ExitCode"] != 0):
            raise RuntimeError("pressure flood failed")
        if drops is not None and baseline_drops is not None and drops < baseline_drops:
            raise RuntimeError("pressure drop counter reset")
        return occupancy, drops

    async def pressure(self, sandbox: str) -> None:
        vector = self.name == "vector-full"
        pressure_started = time.time()
        baseline = await self.telemetry.metrics("vector")
        drops = stage_metric(
            baseline, "vector_buffer_discarded_events_total", "disk", "1", intentional="true"
        )
        if vector:
            sink = await asyncio.to_thread(self.telemetry.container, "victorialogs")
            await self.arm({"sink": sink})
            await self.command("docker", "stop", "--time=3", sink)
        if not vector:
            await self.flood(20)
            while len(await self.accepted()) != 20:
                await asyncio.sleep(0.1)
            baseline_instance = self.flood_instance
            baseline_operation = str(UUID(int=1))
            await self.command("docker", "rm", "--force", str(self.flood_id))
            self.flood_id = None
            self.observations.update(
                retention_baseline_accepted=20,
                retention_baseline_instance=baseline_instance,
                retention_baseline_operation=baseline_operation,
            )
            # A new batch must not re-emit the accepted baseline event identity.
            self.flood_instance = str(uuid4())
        async with AsyncExitStack() as held:
            if not vector:
                await self.command("journalctl", "--namespace=" + self.root.name, "--rotate")
                retained_fd, retained_identity = await held.enter_async_context(
                    retained_archive(
                        self.root / "journal", self.command, baseline_instance, baseline_operation
                    )
                )
                self.observations["retention_archived_file"] = retained_identity
            await self.flood(1000000)
            while True:
                await self.progress(sandbox)
                if vector:
                    metrics = await self.telemetry.metrics("vector")
                    occupancy, current_drops = await self.buffer_pressure(metrics, drops)
                    if current_drops is not None and current_drops > (
                        drops if drops is not None else 0
                    ):
                        usable = self.observations["vector_pressure"]["latest"]["usable_disk_bytes"]
                        if occupancy < 0.8 * usable:
                            raise RuntimeError("drops without measured full-buffer pressure")
                        self.observations.update(
                            buffer_peak_observed=occupancy,
                            discarded_delta=None if drops is None else current_drops - drops,
                            discarded_counter_observed=current_drops,
                            drop_baseline_previously_absent=drops is None,
                        )
                        await self.wait_drop_metric(drops, pressure_started)
                        break
                else:
                    # Rotation plus size-driven deletion, independently of ENOSPC.
                    await self.command("journalctl", "--namespace=" + self.root.name, "--rotate")
                    usage = journal_usage(self.root / "journal")
                    self.observations["journal_usage"] = usage
                    size = usage["allocated_bytes"]
                    if size > 100 * 1024**2:
                        raise RuntimeError("reduced journal size ceiling exceeded")
                    removal = removal_observation(
                        self.root / "journal", retained_fd, retained_identity
                    )
                    self.observations["retention_file_removal"] = removal
                    if removal["removed"] and size >= 32 * 1024**2:
                        self.observations.update(journal_bytes=size, oldest_event_removed=True)
                        break
                await asyncio.sleep(0.5)

    async def wait_drop_metric(self, baseline: float | None, started: float) -> None:
        labels = {
            "job": "vector",
            "component_id": "storage",
            "buffer_id": "storage",
            "buffer_type": "disk",
            "stage": "1",
            "intentional": "true",
        }
        selector = (
            "vector_buffer_discarded_events_total{"
            + ",".join(f'{key}="{value}"' for key, value in labels.items())
            + "}"
        )
        threshold = baseline if baseline is not None else 0
        query = f"({selector} > {threshold}) and (timestamp({selector}) >= {started})"
        self.observations["prometheus_tail_discarded"] = None
        while True:
            data = json.loads(
                await self.telemetry.query("prometheus", "/api/v1/query", params={"query": query})
            )
            if data.get("status") != "success" or data["data"]["resultType"] != "vector":
                raise RuntimeError("invalid stored drop query")
            result = data["data"]["result"]
            if result:
                if len(result) != 1 or any(
                    result[0]["metric"].get(key) != value for key, value in labels.items()
                ):
                    raise RuntimeError("unexpected stored drop series")
                value = float(result[0]["value"][1])
                if not math.isfinite(value) or value <= threshold:
                    raise RuntimeError("invalid stored drop counter")
                self.observations["prometheus_tail_discarded"] = {
                    "labels": labels,
                    "value": value,
                    "fresh_sample_after_fault": True,
                }
                return
            await asyncio.sleep(0.2)

    async def producer_pipe(self, sandbox: str) -> None:
        control = self.root / "control"
        (control / "blocked-stdout").touch()
        await asyncio.to_thread(
            self.app.stack.runtime_command, "up", "--detach", "--force-recreate", "api"
        )
        while not (control / "pipe-active.json").exists():
            self.commands.deadline.remaining()
            await asyncio.sleep(0.1)
        active = json.loads((control / "pipe-active.json").read_text())
        self.observations["pipe_active"] = active
        metrics_url = "http://" + await asyncio.to_thread(
            self.app.stack.runtime_command, "port", "api", "9101"
        )
        self.telemetry.endpoints["api"] = metrics_url
        while True:
            try:
                await self.app.request("GET", "/healthz")
                break
            except Exception:
                self.commands.deadline.remaining()
                await asyncio.sleep(0.1)

        async def load() -> None:
            while True:
                await self.app.request("GET", "/healthz")

        drivers = [asyncio.create_task(load()) for _ in range(16)]
        try:
            while True:
                metrics = await self.telemetry.metrics("api")
                drops = total(metrics, "boxie_log_dropped_total")
                if drops > 0 and total(metrics, "boxie_log_queue_occupancy") == 2048:
                    self.observations["producer_drops_with_full_queue"] = drops
                    break
                await asyncio.sleep(0.2)
            await self.progress(sandbox)
            extra = await self.app.provision()
            await self.progress(extra)
        finally:
            for task in drivers:
                task.cancel()
            await asyncio.gather(*drivers, return_exceptions=True)
        (control / "stop-api").touch()
        while not (control / "pipe-exit.json").exists():
            self.commands.deadline.remaining()
            await asyncio.sleep(0.1)
        result = json.loads((control / "pipe-exit.json").read_text())
        self.observations["pipe_shutdown"] = result
        validate_shutdown(active, result)

    async def run(self) -> None:
        if self.name == "lifecycle-failure":
            await self.failure_case()
            return
        sandbox = await self.app.provision()
        await self.progress(sandbox)
        if self.name in ("collector-restart", "storage-restart"):
            await self.recovery_case(sandbox)
        elif self.name == "journal-stopped":
            await self.journal_stopped(sandbox)
        elif self.name in ("journal-enospc", "vector-enospc"):
            await self.exhaustion(sandbox)
        elif self.name in ("journal-retention", "vector-full"):
            await self.pressure(sandbox)
        elif self.name == "producer-pipe":
            await self.producer_pipe(sandbox)
        else:
            raise ValueError("unknown required scenario")
