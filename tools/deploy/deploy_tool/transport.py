"""Pinned SSH transport; neither provisioning credentials nor secrets in argv."""

import base64
import json
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import cast

from .manifests import ReleaseError, object_map, string
from .prepare import canonical, identity, private_write


def deploy_ssh(
    bundle: dict[str, object], profile: dict[str, object], key: Path, timeout: int
) -> dict[str, str]:
    if not 30 <= timeout <= 900:
        raise ReleaseError("SSH timeout must be between 30 and 900 seconds")
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
    source = Path(__file__).with_name("remote.py").read_text()
    remote_command = shlex.join(["python3", "-c", source, "--timeout", str(timeout - 30)])
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
        try:
            result = subprocess.run(
                command, input=canonical(bundle), capture_output=True, timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired as error:
            raise ReleaseError(
                "SSH deadline exceeded; inspect remote component status before retrying"
            ) from error
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
