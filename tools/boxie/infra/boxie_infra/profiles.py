"""Protected operator-only atomic profile updates with observed digest acknowledgement."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

from boxie.sandbox.profiles import ProfileRegistry


class ProfileUpdateError(Exception):
    """A safe operator diagnostic; profile and credential bytes are never printed."""


def replace_child(directory: Path, content: bytes) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".profiles-", dir=directory)
    staging = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o644)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging, directory / "profiles.json")
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        staging.unlink(missing_ok=True)


def update_profiles(
    directory: Path,
    content: bytes,
    signal_reload: Callable[[], None],
    read_digest: Callable[[], str],
    timeout: float = 30,
) -> str:
    if not 0 < timeout <= 30 or len(content) > 1024 * 1024:
        raise ProfileUpdateError("invalid profile operation bounds")
    ProfileRegistry._parse(content)
    source = directory / "profiles.json"
    if directory.is_symlink() or source.is_symlink() or not source.is_file():
        raise ProfileUpdateError("stable profile directory with a regular profiles.json required")
    original = source.read_bytes()
    ProfileRegistry._parse(original)
    digest = hashlib.sha256(content).hexdigest()
    prior_digest = hashlib.sha256(original).hexdigest()
    lock_fd = os.open(directory / ".profiles.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ProfileUpdateError("another profile update is in progress") from error
        # Read under the operator lock; never restore an older concurrent update.
        original = source.read_bytes()
        prior_digest = hashlib.sha256(original).hexdigest()

        def acknowledge(expected: str, deadline: float) -> None:
            while True:
                try:
                    if read_digest() == expected:
                        return
                except OSError, ValueError, subprocess.SubprocessError:
                    pass
                if time.monotonic() >= deadline:
                    raise ProfileUpdateError("profile digest was not acknowledged")
                time.sleep(min(0.1, max(0, deadline - time.monotonic())))

        deadline = time.monotonic() + timeout
        try:
            replace_child(directory, content)
            signal_reload()
            acknowledge(digest, deadline - timeout * 0.25)
            return digest
        except Exception as error:
            try:
                replace_child(directory, original)
                signal_reload()
                acknowledge(prior_digest, deadline)
            except Exception as restore:
                raise ProfileUpdateError(
                    "recovery-required: profile restoration not acknowledged"
                ) from restore
            raise ProfileUpdateError(
                "profile update failed; previous approvals restored"
            ) from error


# Executed inside the verified running image; it owns schema validation and
# authenticated loopback resolution. Neither operation restarts a container.
REMOTE = """import json,sys,urllib.request
from pathlib import Path
from boxie.bootstrap.config import OrchestratorConfig, read_credential
from boxie.sandbox.profiles import ProfileRegistry
config=OrchestratorConfig.from_file(Path('/etc/boxie/orchestrator.json'))
expected=json.loads(sys.argv[2])
assert str(config.identity.installation_id)==expected['installation_id'] and config.identity.host_id==expected['host_id']
if sys.argv[1]=='validate':
    ProfileRegistry._parse(sys.stdin.buffer.read(1048577))
elif sys.argv[1]=='digest':
    request=urllib.request.Request('http://127.0.0.1:'+str(config.bind_port)+'/v1/profiles/resolve',
        data=sys.stdin.buffer.read(65537),headers={'Authorization':'Bearer '+read_credential(config.credential_file).decode(),'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=2) as response:
        print(json.load(response)['config_digest'])
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--content", type=Path, required=True)
    parser.add_argument("--installation-id", type=UUID, required=True)
    parser.add_argument("--host-id", required=True)
    parser.add_argument("--container", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--command", type=json.loads, required=True)
    args = parser.parse_args()
    identity = json.dumps({"installation_id": str(args.installation_id), "host_id": args.host_id})

    def docker(*argv: str, data: bytes | None = None) -> bytes:
        return subprocess.run(
            ["docker", *argv], input=data, capture_output=True, check=True, timeout=3
        ).stdout

    try:
        if not re.fullmatch(r"[a-f0-9]{64}", args.container) or not re.fullmatch(
            r"[a-z0-9][a-z0-9.:-]*/[a-z0-9][a-z0-9./_-]*@sha256:[a-f0-9]{64}", args.image
        ):
            raise ProfileUpdateError(
                "exact container ID and registry-qualified image digest required"
            )
        info = json.loads(docker("inspect", args.container))[0]
        labels = info["Config"]["Labels"]
        expected_image = (
            docker("image", "inspect", "--format", "{{.Id}}", args.image).decode().strip()
        )
        if (
            info["Image"] != expected_image
            or not info["State"]["Running"]
            or labels.get("com.docker.compose.project") != "clis-boxie"
            or labels.get("com.docker.compose.service") != "orchestrator"
            or not labels.get("clis.release-id")
        ):
            raise ProfileUpdateError("orchestrator ownership or approved image mismatch")
        directory = args.directory.resolve(strict=True)
        if (
            args.directory.is_symlink()
            or directory.stat().st_uid != 0
            or directory.stat().st_mode & 0o022
            or not any(
                m["Source"] == str(directory)
                and m["Destination"] == "/etc/boxie/approved"
                and not m["RW"]
                for m in info["Mounts"]
            )
        ):
            raise ProfileUpdateError("root-owned stable read-only profile mount required")
        content = args.content.read_bytes()
        docker(
            "exec", "-i", args.container, "python", "-c", REMOTE, "validate", identity, data=content
        )
        request = json.dumps(
            {"profile_id": args.profile, "command": args.command, "env": {}}
        ).encode()

        def signal_reload() -> None:
            docker("kill", "--signal", "HUP", args.container)

        def read_digest() -> str:
            return (
                docker(
                    "exec",
                    "-i",
                    args.container,
                    "python",
                    "-c",
                    REMOTE,
                    "digest",
                    identity,
                    data=request,
                )
                .decode()
                .strip()
            )

        print(
            json.dumps(
                {"config_digest": update_profiles(directory, content, signal_reload, read_digest)}
            )
        )
        return 0
    except Exception:
        print(
            "profiles: operation failed; inspect approvals and acknowledged digest before retrying",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
