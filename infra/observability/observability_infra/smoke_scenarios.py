"""Nine concrete faults against one prepared native application/collector assembly."""

import asyncio
import errno
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from observability_infra.smoke_application import REPO, Application
from observability_infra.smoke_runtime import event_identities, evidence
from observability_infra.smoke_telemetry import Telemetry
from observability_infra.smoke_watchdog import process_identity

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


def total(metrics: dict[str, list[dict[str, Any]]], name: str) -> float:
    if name not in metrics:
        raise RuntimeError("required native metric unavailable: " + name)
    return sum(
        float(sample["value"])
        for sample in metrics[name]
        if not name.startswith("vector_") or sample["labels"].get("component_id") == "storage"
    )


class Scenario:
    def __init__(self, name: str, application: Application, telemetry: Telemetry) -> None:
        self.name, self.app, self.telemetry = name, application, telemetry
        self.root, self.commands = application.root, application.commands
        self.observations: dict[str, Any] = {"scenario": name, "synthetic_pressure": False}
        self.watch: subprocess.Popen[bytes] | None = None
        self.manifest = self.root / ("watch-" + name + ".json")
        self.flood_id: str | None = None
        self.flood_instance = str(uuid4())

    async def command(self, *args: str) -> str:
        return await asyncio.to_thread(self.commands.run, list(args), env=self.app.stack.env)

    async def arm(self, data: dict[str, Any]) -> None:
        data["restore_at"] = self.commands.deadline.end - self.commands.deadline.reserve + 2
        evidence(self.manifest, data)
        self.watch = subprocess.Popen(
            [sys.executable, "-m", "observability_infra.smoke_watchdog", str(self.manifest)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.observations["watchdog_pid"] = self.watch.pid

    async def restore(self) -> None:
        if self.watch:
            self.manifest.with_suffix(".restore").touch()
            await asyncio.to_thread(
                self.watch.wait, timeout=min(22, self.commands.deadline.remaining(restoring=True))
            )
            path = self.manifest.with_suffix(".result.json")
            if self.watch.returncode or not path.exists():
                raise RuntimeError("independent restoration watchdog failed")
            restored = json.loads(path.read_text())
            self.observations["restoration"] = restored
            if not all(restored.values()):
                raise RuntimeError("independent restoration incomplete")
        if self.flood_id:
            await self.command("docker", "rm", "--force", self.flood_id)
            self.flood_id = None
        self.app.stack.fault("fail_create", False)
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

    async def flood(self, limit: int) -> None:
        self.observations["synthetic_pressure"] = True
        name = self.root.name + "-flood-" + self.name
        # Register exact unique name before creation; retain actual ID immediately.
        self.observations["flood_name"] = name
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
        )
        self.observations["flood_id"] = self.flood_id

    async def progress(self, sandbox: str) -> None:
        self.observations.setdefault("progress", []).append(await self.app.progress(sandbox))
        self.observations.setdefault("resources", []).append(
            await asyncio.to_thread(self.telemetry.resources)
        )

    async def accepted(self) -> set[str]:
        # A small recovery batch is accepted only if present in real binary journal.
        text = await self.command(
            "journalctl",
            "--directory=" + str(self.root / "journal"),
            "--no-pager",
            "--output=cat",
            "--grep=" + self.flood_instance,
            "--lines=100",
        )
        return event_identities(text, self.flood_instance)

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
        await self.recovered(accepted)
        if service == "vector":
            # Restart same container/state twice: known accepted tail must remain searchable.
            await self.command("docker", "restart", "--time=3", identifier)
            await self.recovered(accepted)
        self.observations["same_container_id"] = identifier
        self.observations["checkpoint_after"] = self.telemetry.checkpoint()

    async def failure_case(self) -> None:
        self.app.stack.fault("fail_create", True)
        sandbox = await self.app.provision(failed=True)
        if not (self.app.stack.root / "faults/failed_create_seen").exists():
            raise RuntimeError("failed-create injection was not reached")
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
        # Query the cached authoritative metric independently of the direct SQL read.
        metric = json.loads(
            await self.telemetry.query(
                "prometheus", "/api/v1/query", params={"query": 'boxie_sandboxes{state="failed"}'}
            )
        )
        while not metric["data"]["result"]:
            await asyncio.sleep(0.2)
            metric = json.loads(
                await self.telemetry.query(
                    "prometheus",
                    "/api/v1/query",
                    params={"query": 'boxie_sandboxes{state="failed"}'},
                )
            )
        if not any(float(item["value"][1]) >= 1 for item in metric["data"]["result"]):
            raise RuntimeError("authoritative failed metric missing")
        rules = json.loads(await self.telemetry.query("prometheus", "/api/v1/rules"))
        if not rules["data"]["groups"] or any(
            rule.get("health") != "ok"
            for group in rules["data"]["groups"]
            for rule in group["rules"]
        ):
            raise RuntimeError("native rules unavailable/unhealthy")
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
            rule_groups=len(rules["data"]["groups"]),
            logs_target_bytes=268435456,
            logs_max_age_days=7,
        )
        self.observations["privacy"] = await self.telemetry.inspect_privacy(
            self.app.sentinels, sandbox
        )

    async def journal_stopped(self, sandbox: str) -> None:
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
        self.observations["receiver_stopped_through_probes"] = True
        self.observations["docker_drop_counter"] = "not exported; no invented drop metric"

    async def exhaustion(self, sandbox: str) -> None:
        vector = self.name == "vector-enospc"
        directory = self.root / ("vector" if vector else "journal")
        filler = directory / ("filler-" + self.name)
        with filler.open("xb"):
            pass
        data: dict[str, Any] = {"fillers": [{"path": str(filler), "inode": filler.stat().st_ino}]}
        if vector:
            data["sink"] = await asyncio.to_thread(self.telemetry.container, "victorialogs")
        await self.arm(data)
        if vector:
            await self.command("docker", "stop", "--time=3", data["sink"])
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
        await self.flood(500000)
        await self.progress(sandbox)
        if vector:
            while True:
                diagnostics = await asyncio.to_thread(
                    self.telemetry.compose, "logs", "--no-color", "--tail=100", "vector"
                )
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
        filler.unlink()
        if vector:
            identifier = await asyncio.to_thread(self.telemetry.container, "vector")
            await self.command("docker", "restart", "--time=3", identifier)
            await self.command("docker", "start", data["sink"])
            self.observations["same_state_restart_id"] = identifier
        self.observations["free_after_release"] = (
            os.statvfs(directory).f_bavail * os.statvfs(directory).f_frsize
        )

    async def pressure(self, sandbox: str) -> None:
        vector = self.name == "vector-full"
        baseline = await self.telemetry.metrics("vector")
        drops = (
            total(baseline, "vector_buffer_discarded_events_total")
            if "vector_buffer_discarded_events_total" in baseline
            else 0
        )
        if vector:
            sink = await asyncio.to_thread(self.telemetry.container, "victorialogs")
            await self.arm({"sink": sink})
            await self.command("docker", "stop", "--time=3", sink)
        if not vector:
            await self.flood(20)
            while len(await self.accepted()) != 20:
                await asyncio.sleep(0.1)
            await self.command("docker", "rm", "--force", str(self.flood_id))
            self.flood_id = None
            self.observations["retention_baseline_accepted"] = 20
        await self.flood(1000000)
        while True:
            await self.progress(sandbox)
            if vector:
                metrics = await self.telemetry.metrics("vector")
                occupancy = total(metrics, "vector_buffer_size_bytes")
                current_drops = (
                    total(metrics, "vector_buffer_discarded_events_total")
                    if "vector_buffer_discarded_events_total" in metrics
                    else 0
                )
                if current_drops > drops:
                    if occupancy < 0.8 * 268435488:
                        raise RuntimeError("drops without measured full-buffer pressure")
                    self.observations.update(
                        buffer_peak_observed=occupancy, discarded_delta=current_drops - drops
                    )
                    break
            else:
                # Rotation plus size-driven deletion, independently of ENOSPC.
                await self.command("journalctl", "--namespace=" + self.root.name, "--rotate")
                size = sum(
                    path.stat().st_size for path in (self.root / "journal").glob("*.journal*")
                )
                if size > 100 * 1024**2:
                    raise RuntimeError("reduced journal size ceiling exceeded")
                text = await self.command(
                    "journalctl",
                    "--directory=" + str(self.root / "journal"),
                    "--no-pager",
                    "--output=cat",
                    "--grep=" + self.flood_instance,
                    "--lines=1",
                    "--reverse",
                )
                if text and json.loads(text.splitlines()[0])["operation_id"] != str(UUID(int=1)):
                    # Query the original first event exactly; disappearance is retention evidence.
                    first = await self.command(
                        "journalctl",
                        "--directory=" + str(self.root / "journal"),
                        "--no-pager",
                        "--output=cat",
                        "--grep=" + str(UUID(int=1)),
                        "--lines=1",
                    )
                    if not first and size >= 32 * 1024**2:
                        self.observations.update(journal_bytes=size, oldest_event_removed=True)
                        break
            await asyncio.sleep(0.5)

    async def producer_pipe(self, sandbox: str) -> None:
        control = self.root / "control"
        (control / "blocked-stdout").touch()
        await asyncio.to_thread(
            self.app.stack.runtime_command, "up", "--detach", "--force-recreate", "api"
        )
        while not (control / "pipe-active.json").exists():
            self.commands.deadline.remaining()
            await asyncio.sleep(0.1)
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
        if result["forced"] or result["exit"] != 0 or result["seconds"] > 8:
            raise RuntimeError("production API did not shut down while stdout unread")
        self.observations["pipe_shutdown"] = result

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
