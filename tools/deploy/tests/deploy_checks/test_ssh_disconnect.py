"""A real non-PTY SSH disconnect must leave no mutating child beyond the remote budget."""

import json
import os
import pwd
import shlex
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from deploy_tool.prepare import load_prepared

from deploy_checks.test_activation import prepare_release
from deploy_checks.test_cli import PACKAGE


@pytest.mark.integration
def test_ssh_disconnect_keeps_remote_mutation_bounded(source: Path) -> None:
    assert sys.platform == "linux", "SSH-disconnect gate requires Linux and openssh-server"
    sshd = shutil.which("sshd") or "/usr/sbin/sshd"
    assert Path(sshd).is_file(), "install openssh-server before selecting the disconnect gate"
    for name in ("host", "client"):
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(source / name)], check=True
        )
    authorized = source / "authorized_keys"
    authorized.write_bytes((source / "client.pub").read_bytes())
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    user = pwd.getpwuid(os.getuid()).pw_name
    config = source / "sshd_config"
    config.write_text(f"""ListenAddress 127.0.0.1
Port {port}
HostKey {source / "host"}
PidFile {source / "sshd.pid"}
AuthorizedKeysFile {authorized}
StrictModes no
PasswordAuthentication no
KbdInteractiveAuthentication no
UsePAM no
PermitRootLogin prohibit-password
AllowUsers {user}
LogLevel ERROR
""")
    descriptor = json.loads((source / "descriptor.json").read_text())
    descriptor["pre_activate"] = "disconnect.sh"
    (source / "descriptor.json").write_text(json.dumps(descriptor))
    marker, mutation = source / "started", source / "survived"
    (source / "disconnect.sh").write_text(
        f"echo started > {shlex.quote(str(marker))}\n(sleep 5; touch {shlex.quote(str(mutation))}) &\nwait\n"
    )
    # A real SSH server executes the shipped adapter. Docker preflight is a fixed
    # successful command; no actual daemon mutation is needed to expose this defect.
    bin_dir = source / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "docker").chmod(0o700)
    prepared = prepare_release(source, "disconnect-prepared")
    remote = shlex.join(
        [
            sys.executable,
            str(PACKAGE / "deploy_tool/remote.py"),
            "--root",
            str(source / "releases"),
            "--timeout",
            "2",
        ]
    )
    remote = f"PATH={shlex.quote(str(bin_dir))}:$PATH {remote}"
    server = subprocess.Popen(
        [sshd, "-D", "-e", "-f", str(config)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )
    client = None
    try:
        end = time.monotonic() + 5
        while True:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                assert server.poll() is None, (
                    "isolated sshd failed to start; check /run/sshd and host permissions"
                )
                assert time.monotonic() < end
                time.sleep(0.05)
        client = subprocess.Popen(
            [
                "ssh",
                "-F",
                "/dev/null",
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
                "-i",
                str(source / "client"),
                "-p",
                str(port),
                f"{user}@127.0.0.1",
                remote,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        assert client.stdin is not None
        client.stdin.write(json.dumps(load_prepared(prepared)).encode())
        client.stdin.close()
        end = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < end:
            time.sleep(0.02)
        assert marker.exists(), "SSH session did not start the owned hook"
        client.kill()
        client.wait()
        time.sleep(5.2)
        assert not mutation.exists(), "a disconnected activation child survived its remote deadline"
        status = json.loads((source / "releases/sample/status.json").read_text())
        assert status["outcome"] in {"recovery-required", "hook-failed"}
    finally:
        if client is not None and client.poll() is None:
            client.kill()
            client.wait()
        server.terminate()
        try:
            server.wait(timeout=3)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
        if server.stderr:
            server.stderr.close()
