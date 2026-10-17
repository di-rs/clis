"""Pinned SSH transport; neither provisioning credentials nor secrets in argv."""

import base64
import json
import os
import selectors
import shlex
import signal
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from .manifests import ReleaseError, object_map, string
from .prepare import canonical, identity, private_write


@contextmanager
def pinned_ssh(profile: dict[str, object], key: Path, remote_command: str) -> Iterator[list[str]]:
    host = string(profile.get("host"), "deployment host", r"[a-zA-Z0-9][a-zA-Z0-9.:-]*")
    user = string(profile.get("user", "root"), "SSH user", r"[a-z_][a-z0-9_-]*")
    port = profile.get("port", 22)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ReleaseError("invalid SSH port")
    string(profile.get("identity_ref"), "SSH identity reference")
    public_key = string(profile.get("host_public_key"), "SSH host public key")
    parts = public_key.split()
    if len(parts) < 2 or parts[0] not in (
        "ssh-ed25519",
        "ssh-rsa",
        "ecdsa-sha2-nistp256",
        "ecdsa-sha2-nistp384",
        "ecdsa-sha2-nistp521",
    ):
        raise ReleaseError("invalid SSH host public key")
    try:
        if len(base64.b64decode(parts[1], validate=True)) < 32:
            raise ReleaseError("invalid SSH host public key")
    except ValueError as error:
        raise ReleaseError("invalid SSH host public key") from error
    if (
        not key.is_file()
        or key.is_symlink()
        or key.stat().st_mode & 0o077
        or not key.stat().st_size
    ):
        raise ReleaseError("SSH identity must be a private regular file with mode 0600")
    with tempfile.TemporaryDirectory(prefix="clis-deploy-ssh-") as temporary:
        known_hosts = Path(temporary) / "known_hosts"
        known_name = host if port == 22 else f"[{host}]:{port}"
        private_write(known_hosts, f"{known_name} {parts[0]} {parts[1]}\n".encode())
        command = [
            "ssh",
            "-F",
            "/dev/null",
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "IdentityAgent=none",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={known_hosts}",
            "-o",
            "GlobalKnownHostsFile=/dev/null",
            "-o",
            "ConnectTimeout=15",
            "-o",
            "ConnectionAttempts=1",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=2",
            "-p",
            str(port),
            "-i",
            str(key.resolve()),
            f"{user}@{host}",
            remote_command,
        ]
        yield command


def deploy_ssh(
    bundle: dict[str, object], profile: dict[str, object], key: Path, timeout: int
) -> dict[str, str]:
    if not 60 <= timeout <= 900:
        raise ReleaseError("SSH timeout must be between 60 and 900 seconds")
    source = Path(__file__).with_name("remote.py").read_text()
    remote_command = shlex.join(["python3", "-c", source, "--timeout", str(timeout - 30)])
    with pinned_ssh(profile, key, remote_command) as command:
        result = run_ssh(command, canonical(bundle), timeout)
        if len(result.stdout) > 4096:
            raise ReleaseError("invalid SSH activation response")
        try:
            response = object_map(json.loads(result.stdout), "activation response")
        except (ValueError, UnicodeError) as error:
            raise ReleaseError(
                "SSH transport failed; inspect remote component status before retrying"
            ) from error
        allowed = {
            "activated",
            "incompatible",
            "preflight-failed",
            "hook-failed",
            "failed-no-previous",
            "failed-rolled-back",
            "failed-rollback-failed",
            "activation-error",
            "recovery-required",
        }
        outcome = response.get("outcome")
        if outcome not in allowed:
            raise ReleaseError("invalid SSH activation response")
        descriptor = object_map(bundle["descriptor"], "descriptor")
        if outcome != "activated" or result.returncode != 0:
            raise ReleaseError(
                f"component activation failed ({outcome}); inspect remote component status"
            )
        if response.get("component") != descriptor["component"] or response.get(
            "release_id"
        ) != identity(bundle):
            raise ReleaseError("SSH activation returned a different release identity")
        return {
            "component": cast(str, descriptor["component"]),
            "release_id": identity(bundle),
            "outcome": "activated",
        }


def run_ssh(command: list[str], payload: bytes, timeout: int) -> subprocess.CompletedProcess[bytes]:
    """Bound both streams during execution, including on early stdin close."""
    with tempfile.TemporaryFile() as incoming:
        incoming.write(payload)
        incoming.seek(0)
        process = subprocess.Popen(
            command,
            stdin=incoming,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        assert process.stdout is not None and process.stderr is not None
        output: dict[int, bytearray] = {
            process.stdout.fileno(): bytearray(),
            process.stderr.fileno(): bytearray(),
        }
        end = time.monotonic() + timeout
        try:
            with selectors.DefaultSelector() as selector:
                for stream in (process.stdout, process.stderr):
                    selector.register(stream, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = end - time.monotonic()
                    if remaining <= 0:
                        raise ReleaseError(
                            "SSH deadline exceeded; inspect remote component status before retrying"
                        )
                    for key, _ in selector.select(min(0.2, remaining)):
                        chunk = os.read(key.fd, 4096)
                        if not chunk:
                            selector.unregister(key.fd)
                            continue
                        stream_output = output[key.fd]
                        if len(stream_output) + len(chunk) > 4096:
                            raise ReleaseError(
                                "SSH output exceeds limit; inspect remote component status"
                            )
                        stream_output.extend(chunk)
            process.wait(timeout=max(0, end - time.monotonic()))
            return subprocess.CompletedProcess(
                command,
                process.returncode,
                bytes(output[process.stdout.fileno()]),
                bytes(output[process.stderr.fileno()]),
            )
        except subprocess.TimeoutExpired as error:
            raise ReleaseError(
                "SSH deadline exceeded; inspect remote component status before retrying"
            ) from error
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            process.stdout.close()
            process.stderr.close()
