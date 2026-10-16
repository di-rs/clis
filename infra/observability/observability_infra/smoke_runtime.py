"""Finite subprocesses and bounded, nonsecret evidence for the native smoke."""

import contextlib
import json
import os
import selectors
import signal
import subprocess
import time
from pathlib import Path
from typing import Any


class Deadline:
    def __init__(self, seconds: float, *, reserve: float = 30) -> None:
        if not 0 < reserve < seconds:
            raise ValueError("deadline must include a positive restoration reserve")
        self.started = time.monotonic()
        self.end = self.started + seconds
        self.reserve = reserve

    def remaining(self, *, restoring: bool = False) -> float:
        value = self.end - time.monotonic() - (0 if restoring else self.reserve)
        if value <= 0:
            raise TimeoutError("smoke deadline")
        return value

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started


def command_diagnostic(args: list[str], output: bytes, code: int, elapsed: float) -> dict[str, Any]:
    # Fixed signatures retain actionable categories without exporting arbitrary output.
    text = output.decode("utf-8", errors="replace").lower()
    signatures = {
        "image_manifest": ("manifest unknown", "no matching manifest", "manifest not found"),
        "registry_auth": ("unauthorized", "pull access denied", "authentication required"),
        "registry_limit": ("toomanyrequests", "rate limit"),
        "network_pool": ("address pools", "pool overlaps", "address space"),
        "network_bridge": (
            "failed to create network",
            "failed to create bridge",
            "iptables",
            "nftables",
        ),
        "port_allocation": ("port is already allocated", "address already in use"),
        "compose_configuration": (
            "invalid interpolation",
            "required variable",
            "validating ",
            "unknown flag",
            "unknown command",
        ),
        "unhealthy": ("unhealthy", "didn't become healthy", "did not become healthy"),
        "permission": ("permission denied", "operation not permitted"),
        "mount": ("error mounting", "invalid mount", "mount denied"),
        "runtime": ("failed to create shim", "oci runtime", "failed to start shim"),
        "capacity": ("no space left", "out of memory", "cannot allocate memory"),
        "connection": (
            "connection refused",
            "connection reset",
            "i/o timeout",
            "context deadline exceeded",
        ),
    }
    executable = Path(args[0]).name
    operation = (
        executable if executable in {"docker", "systemctl", "journalctl", "cargo"} else "subprocess"
    )
    if executable == "docker" and "compose" in args:
        operation += ".compose." + next(
            (
                value
                for value in ("up", "down", "config", "logs", "exec", "ps", "port", "build")
                if value in args
            ),
            "other",
        )
    return {
        "operation": operation,
        "returncode": code,
        "elapsed_seconds": elapsed,
        "classification": [
            name for name, tokens in signatures.items() if any(token in text for token in tokens)
        ]
        or ["unclassified"],
        "captured_bytes": len(output),
        "raw_output_exported": False,
    }


class PrivacyScan:
    """Inspect full newline records, retaining at most one record and a read chunk."""

    record_limit = 65536

    def __init__(self, sentinels: tuple[str, ...], max_records: int) -> None:
        if not 1 <= max_records <= 200:
            raise ValueError("privacy record bound required")
        self.sentinels, self.max_records = sentinels, max_records
        self.pending = bytearray()
        self.records = self.total = 0

    def feed(self, chunk: bytes) -> None:
        self.total += len(chunk)
        if self.total > self.max_records * self.record_limit:
            raise RuntimeError("smoke privacy byte limit exceeded")
        self.pending.extend(chunk)
        while (end := self.pending.find(b"\n")) >= 0:
            if end + 1 > self.record_limit:
                raise RuntimeError("smoke privacy record limit exceeded")
            self.records += 1
            if self.records > self.max_records:
                raise RuntimeError("smoke privacy record count exceeded")
            # Do not replace invalid bytes: an undecodable record is not proof.
            try:
                text = self.pending[:end].decode("utf-8")
            except UnicodeError:
                raise RuntimeError("smoke privacy record decoding failed") from None
            check_privacy(text, self.sentinels)
            del self.pending[: end + 1]
        if len(self.pending) >= self.record_limit:
            raise RuntimeError("smoke privacy record limit exceeded")

    def finish(self) -> dict[str, int]:
        if self.pending:
            raise RuntimeError("smoke privacy scan incomplete")
        return {"records": self.records, "bytes": self.total}


class Commands:
    """Capture a small result; never put command arguments/output in exceptions."""

    def __init__(self, deadline: Deadline, *, output_limit: int = 262144) -> None:
        self.deadline = deadline
        self.output_limit = output_limit
        self.restoring = False
        self.last_failure: dict[str, Any] | None = None
        # Private diagnostic input only. Never included directly in evidence or exceptions.
        self.failed_setup_output: bytes | None = None

    def run(
        self, args: list[str], *, env: dict[str, str] | None = None, empty_journal_ok: bool = False
    ) -> str:
        return self._run(args, env=env, empty_journal_ok=empty_journal_ok)

    def scan_privacy(
        self,
        args: list[str],
        sentinels: tuple[str, ...],
        *,
        max_records: int,
        env: dict[str, str] | None = None,
    ) -> dict[str, int]:
        scan = PrivacyScan(sentinels, max_records)
        self._run(args, env=env, scan=scan)
        return scan.finish()

    def _run(
        self,
        args: list[str],
        *,
        env: dict[str, str] | None,
        empty_journal_ok: bool = False,
        scan: PrivacyScan | None = None,
    ) -> str:
        started = time.monotonic()
        self.failed_setup_output = None
        restoring = self.restoring
        self.deadline.remaining(restoring=restoring)
        process = subprocess.Popen(
            args,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        assert process.stdout is not None
        output = bytearray()
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = self.deadline.remaining(restoring=restoring)
                    for key, _ in selector.select(min(remaining, 0.1)):
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fd)
                        else:
                            if scan is not None:
                                scan.feed(chunk)
                            else:
                                output.extend(chunk)
                                if len(output) > self.output_limit:
                                    raise RuntimeError("smoke command output limit exceeded")
                process.wait(timeout=self.deadline.remaining(restoring=restoring))
            # systemd255 --output=cat implies quiet: grep no-match exits1 with
            # no banner. Accept only that exact empty result or the normal banner;
            # stderr is merged, so permission/parse/read diagnostics still fail.
            if (
                empty_journal_ok
                and Path(args[0]).name == "journalctl"
                and any(arg.startswith("--grep=") for arg in args)
                and process.returncode == 1
                and (
                    output.strip() == b"-- No entries --" or ("--output=cat" in args and not output)
                )
            ):
                return ""
            if process.returncode:
                self.last_failure = command_diagnostic(
                    args, bytes(output), process.returncode, time.monotonic() - started
                )
                if self.last_failure["operation"] in {
                    "docker.compose.build",
                    "docker.compose.port",
                }:
                    self.failed_setup_output = bytes(output)
                raise RuntimeError(f"smoke command exit {process.returncode}")
            return output.decode("utf-8", errors="replace").strip()
        finally:
            # Kill the whole session, including any descendants holding the pipe.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=1)
            process.stdout.close()


def check_privacy(text: str, sentinels: tuple[str, ...]) -> None:
    decoded: list[str] = [text]
    for line in text.splitlines():
        try:
            decoded.append(json.dumps(json.loads(line), ensure_ascii=False))
        except ValueError, TypeError:
            continue
    if any(secret in item for secret in sentinels for item in decoded):
        raise RuntimeError("smoke privacy assertion failed")


def evidence(path: Path, value: Any) -> None:
    """Atomic bounded allowlisted observations, never arbitrary command dumps."""
    data = json.dumps(value, indent=2, sort_keys=True).encode()
    if len(data) > 262144:
        raise RuntimeError("smoke evidence limit exceeded")
    temporary = path.with_suffix(".pending")
    temporary.touch(mode=0o600, exist_ok=True)
    temporary.chmod(0o600)
    temporary.write_bytes(data)
    temporary.replace(path)


def event_identities(text: str, instance: str) -> set[str]:
    """A parser comparison helper; JSONL itself is never journal ingestion proof."""
    from uuid import UUID

    identities = set()
    for line in text.splitlines():
        event = json.loads(line)
        if event.get("instance_id") == instance:
            identities.add(str(UUID(event["operation_id"])))
    return identities


def cleanup_phase(root: Path, phase: str) -> None:
    if phase not in {
        "restoration",
        "scenario_units",
        "telemetry",
        "application",
        "resources",
        "owned_inventory",
        "owned_services",
        "owned_mounts",
        "owned_loops",
        "owned_files",
        "private_files",
        "complete",
    }:
        raise ValueError("unknown cleanup phase")
    evidence(
        root / "cleanup-progress.json",
        {"phase": phase, "status": "complete" if phase == "complete" else "in_progress"},
    )
    print(json.dumps({"cleanup_phase": phase}), flush=True)
