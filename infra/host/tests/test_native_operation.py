"""Opt-in systemd acceptance on a disposable native Linux host.

Run as root with CLIS_NATIVE_OPERATION_TESTS=1. Real SSH disconnection and reboot
remain separate host acceptance steps; these tests interrupt the systemctl client.
"""

import os
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.skipif(
    os.environ.get("CLIS_NATIVE_OPERATION_TESTS") != "1",
    reason="requires an explicitly selected disposable native systemd host",
)


def systemctl(*args: str) -> str:
    return subprocess.check_output(["systemctl", *args], text=True).strip()


def wait_for(check: Callable[[], bool]) -> None:
    # Bounded observation of test units, not a deployment transport retry.
    deadline = time.monotonic() + 15
    while not check():
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for systemd operation")
        time.sleep(0.05)


@pytest.fixture
def native_operation(tmp_path: Path) -> Iterator[tuple[str, Path, Path]]:
    assert os.uname().sysname == "Linux" and os.geteuid() == 0
    assert Path("/run/systemd/system").is_dir()
    name = "clis-acceptance-" + uuid4().hex
    unit = Path("/run/systemd/system") / (name + "@.service")
    state = tmp_path / "state"
    releases = tmp_path / "releases"
    release = releases / "boxie/1-1"
    owner = release / "tools/boxie/infra"
    owner.mkdir(parents=True)
    (release / "revision").write_text("a" * 40)
    (release / "expected-revision").write_text("none")
    (release / "image.env").write_text(
        "BOXIE_RUNTIME_IMAGE=ghcr.io/test/boxie@sha256:" + "a" * 64 + "\n"
    )
    template = (ROOT / "infra/host/systemd/clis-boxie@.service").read_text()
    template = (
        template.replace("Requires=docker.service\n", "")
        .replace("After=docker.service\n", "")
        .replace("RequiresMountsFor=/srv/clis\n", "")
    )
    template = template.replace(
        "ExecStartPre=/usr/local/sbin/clis-storage-guard", "ExecStartPre=/bin/true"
    )
    template = template.replace("/run/lock/clis-deployment.lock", str(tmp_path / "lock"))
    template = template.replace(
        "/usr/local/sbin/clis-operation", str(ROOT / "infra/host/operation.sh")
    )
    template += f"Environment=CLIS_RELEASE_ROOT={releases}\nEnvironment=CLIS_STATE_DIR={state}\n"
    unit.write_text(template)
    systemctl("daemon-reload")
    try:
        yield name + "@1-1.service", owner / "deploy.sh", state
    finally:
        subprocess.run(["systemctl", "stop", name + "@1-1.service"], check=False)
        subprocess.run(["systemctl", "reset-failed", name + "@1-1.service"], check=False)
        unit.unlink()
        systemctl("daemon-reload")


def test_unit_finishes_after_initiating_client_exits(
    native_operation: tuple[str, Path, Path],
) -> None:
    unit, script, state = native_operation
    script.write_text("sleep 2\n")
    with subprocess.Popen(["systemctl", "start", unit]) as client:
        wait_for(lambda: (state / "pending").exists())
        client.terminate()
        client.wait(timeout=5)
    wait_for(lambda: systemctl("show", unit, "-p", "SubState", "--value") == "exited")
    assert systemctl("show", unit, "-p", "Result", "--value") == "success"
    assert not (state / "pending").exists()
    assert (state / "boxie/current/revision").read_text() == "a" * 40


def test_timeout_kills_children_and_preserves_pending(
    native_operation: tuple[str, Path, Path],
) -> None:
    unit, script, state = native_operation
    pidfile = script.parent / "child-pid"
    script.write_text(f'sleep 60 &\nprintf "%s" "$!" > "{pidfile}"\nwait\n')
    # Override only the deadline, retaining the production control-group kill policy.
    unit_file = Path("/run/systemd/system") / (unit.split("@")[0] + "@.service")
    unit_file.write_text(unit_file.read_text().replace("TimeoutStartSec=900", "TimeoutStartSec=1"))
    systemctl("daemon-reload")
    result = subprocess.run(["systemctl", "start", unit], check=False)
    assert result.returncode != 0
    assert systemctl("show", unit, "-p", "Result", "--value") == "timeout"
    assert (state / "pending").exists()
    assert not (state / "boxie/current").exists()
    wait_for(lambda: not Path(f"/proc/{pidfile.read_text()}").exists())
