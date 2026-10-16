"""Finite subprocesses and bounded, nonsecret evidence for the native smoke."""

import contextlib
import json
import os
import re
import selectors
import signal
import subprocess
import time
from dataclasses import dataclass
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
    result = {
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
    if executable == "journalctl":
        stages = {
            "failed to iterate through journal: ": "iteration",
            "failed to get message field: ": "message_read",
            "bad pattern ": "pattern_compile",
            "pattern matching failed: ": "pattern_match",
        }
        reasons = {
            "bad message": "bad_message",
            "cannot assign requested address": "address_unavailable",
            "invalid argument": "invalid_argument",
            "input/output error": "io_error",
            "identifier removed": "identifier_removed",
            "permission denied": "permission",
            "no such file or directory": "not_found",
            "cannot allocate memory": "memory",
            "match limit exceeded": "match_limit",
        }
        for line in text.splitlines():
            for prefix, stage in stages.items():
                if line.startswith(prefix):
                    result["journal_error"] = {
                        "stage": stage,
                        "reason": reasons.get(line.removeprefix(prefix), "unclassified"),
                    }
                    return result
    return result


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


@dataclass(frozen=True)
class JournalRead:
    text: str
    observation: dict[str, Any]


@dataclass(frozen=True)
class _JournalOutput:
    stdout: bytes
    stderr: bytes
    returncode: int


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
        result = self._run(args, env=env, empty_journal_ok=empty_journal_ok)
        assert isinstance(result, str)
        return result

    def read_journal(
        self,
        args: list[str],
        *,
        directory: Path,
        allow_incomplete: bool = False,
        env: dict[str, str] | None = None,
    ) -> JournalRead:
        if (
            Path(args[0]).name != "journalctl"
            or "--directory=" + str(directory) not in args
            or "--output=cat" not in args
            or not any(arg.startswith("--grep=") for arg in args)
        ):
            raise ValueError("bounded journal directory read required")
        started = time.monotonic()
        result = self._run(args, env=env, journal=True)
        assert isinstance(result, _JournalOutput)
        warnings: dict[str, int] = {}
        valid = (not result.stderr or result.stderr.endswith(b"\n")) and len(
            result.stderr.splitlines()
        ) <= 128
        if result.stdout and not result.stdout.endswith(b"\n"):
            raise RuntimeError("journal record stream incomplete")
        try:
            text = result.stdout.decode("utf-8").strip()
            diagnostic = result.stderr.decode("utf-8")
        except UnicodeError:
            raise RuntimeError("journal stream decoding failed") from None
        for line in diagnostic.splitlines():
            match = re.fullmatch(
                r"Journal file (.+) (is truncated|corrupted), ignoring file\.", line
            )
            if match is None:
                valid = False
                break
            path = Path(match[1])
            if (
                path.parent != directory
                or str(path) != match[1]
                or not path.name.endswith((".journal", ".journal~"))
                or any(ord(char) < 32 for char in match[1])
            ):
                valid = False
                break
            category = "truncated_file" if match[2] == "is truncated" else "corrupted_file"
            warnings[category] = warnings.get(category, 0) + 1
        observation = {
            "complete": not result.stderr
            and (result.returncode == 0 or (result.returncode == 1 and not result.stdout)),
            "warnings": warnings,
            "stdout_bytes": len(result.stdout),
            "stderr_bytes": len(result.stderr),
            "returncode": result.returncode,
            "output_provenance": "stdout_only",
        }
        # v255 grep no-match exits 1 after a successful read, including when
        # access-check warnings describe ignored files. Such a result is partial,
        # never absence proof, and is allowed only by the ENOSPC caller.
        no_match = result.returncode == 1 and not result.stdout
        if (
            not valid
            or (result.stderr and not allow_incomplete)
            or (result.returncode != 0 and not no_match)
        ):
            self.last_failure = command_diagnostic(
                args, result.stderr, result.returncode, time.monotonic() - started
            )
            self.last_failure["journal_read"] = observation
            raise RuntimeError("journal read incomplete or failed")
        return JournalRead(text, observation)

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
        journal: bool = False,
    ) -> str | _JournalOutput:
        started = time.monotonic()
        self.failed_setup_output = None
        restoring = self.restoring
        self.deadline.remaining(restoring=restoring)
        process = subprocess.Popen(
            args,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE if journal else subprocess.STDOUT,
            start_new_session=True,
        )
        assert process.stdout is not None
        output = bytearray()
        diagnostics = bytearray()
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                if journal:
                    assert process.stderr is not None
                    selector.register(process.stderr, selectors.EVENT_READ, data="stderr")
                while selector.get_map():
                    remaining = self.deadline.remaining(restoring=restoring)
                    for key, _ in selector.select(min(remaining, 0.1)):
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fd)
                        else:
                            if key.data == "stderr":
                                diagnostics.extend(chunk)
                                if (
                                    len(diagnostics) > 16384
                                    or len(output) + len(diagnostics) > self.output_limit
                                ):
                                    raise RuntimeError("smoke journal output limit exceeded")
                            elif scan is not None:
                                scan.feed(chunk)
                            else:
                                output.extend(chunk)
                                if len(output) + len(diagnostics) > self.output_limit:
                                    raise RuntimeError("smoke command output limit exceeded")
                process.wait(timeout=self.deadline.remaining(restoring=restoring))
            if journal:
                return _JournalOutput(bytes(output), bytes(diagnostics), process.returncode)
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
            if process.stderr is not None:
                process.stderr.close()


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


class EventIdentityError(ValueError):
    """A fatal record failure carrying only bounded structural evidence."""

    def __init__(self, detail: dict[str, Any]) -> None:
        super().__init__("journal event identity decoding failed")
        self.detail = detail


def event_identities(text: str, instance: str) -> set[str]:
    """A parser comparison helper; JSONL itself is never journal ingestion proof."""
    from uuid import UUID

    identities = set()
    for number, line in enumerate(text.splitlines(), 1):
        detail: dict[str, Any] = {"line_number": number, "line_bytes": len(line.encode())}
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            categories = {
                "Expecting value": "value",
                "Expecting ',' delimiter": "delimiter",
                "Expecting ':' delimiter": "delimiter",
                "Expecting property name enclosed in double quotes": "property_name",
                "Unterminated string starting at": "unterminated_string",
                "Invalid control character at": "control_character",
                "Invalid \\escape": "escape",
                "Invalid \\uXXXX escape": "unicode_escape",
                "Extra data": "extra_data",
            }
            detail.update(category=categories.get(error.msg, "json_other"), position=error.pos)
            diagnostic = command_diagnostic(["journalctl"], line.encode(), 0, 0)
            detail["journal_diagnostic"] = diagnostic.get("journal_error")
            detail["classification"] = diagnostic["classification"]
            raise EventIdentityError(detail) from None
        if not isinstance(event, dict):
            raise EventIdentityError(dict(detail, category="event_shape"))
        if event.get("instance_id") == instance:
            try:
                identities.add(str(UUID(event["operation_id"])))
            except KeyError, ValueError, TypeError, AttributeError:
                raise EventIdentityError(dict(detail, category="operation_identity")) from None
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
