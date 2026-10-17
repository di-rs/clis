"""A failed local operation leaves evidence and blocks a subsequent mutation."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("status", [0, 17])
def test_operation_records_success_or_preserves_pending(tmp_path: Path, status: int) -> None:
    releases = tmp_path / "releases"
    release = releases / "boxie/123-1"
    owner = release / "tools/boxie/infra"
    owner.mkdir(parents=True)
    (owner / "deploy.sh").write_text(f"#!/bin/bash\nexit {status}\n")
    (release / "revision").write_text("a" * 40 + "\n")
    (release / "expected-revision").write_text("none\n")
    (release / "image.env").write_text(
        "BOXIE_RUNTIME_IMAGE=ghcr.io/test/boxie@sha256:" + "b" * 64 + "\n"
    )
    state = tmp_path / "state"
    env = {**os.environ, "CLIS_RELEASE_ROOT": str(releases), "CLIS_STATE_DIR": str(state)}
    result = subprocess.run(
        ["bash", str(ROOT / "infra/host/operation.sh"), "boxie", "123-1"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == status, result.stderr
    assert (state / "pending").exists() == bool(status)
    current = state / "boxie/current"
    assert current.exists() == (status == 0)
    if status:
        (owner / "deploy.sh").write_text("exit 0\n")
        retried = subprocess.run(
            ["bash", str(ROOT / "infra/host/operation.sh"), "boxie", "123-1"],
            env=env,
            capture_output=True,
            text=True,
        )
        assert retried.returncode != 0
        assert not current.exists()
    else:
        assert current.resolve() == release


def test_operation_rejects_shell_text_in_instance(tmp_path: Path) -> None:
    result = subprocess.run(
        ["bash", str(ROOT / "infra/host/operation.sh"), "boxie", "1;touch injected"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert not (tmp_path / "injected").exists()


def test_operation_rejects_changed_success_record(tmp_path: Path) -> None:
    release = tmp_path / "releases/boxie/1-1"
    release.mkdir(parents=True)
    (release / "revision").write_text("a" * 40)
    (release / "expected-revision").write_text("b" * 40)
    (release / "image.env").write_text(
        "BOXIE_RUNTIME_IMAGE=ghcr.io/test/boxie@sha256:" + "c" * 64 + "\n"
    )
    state = tmp_path / "state"
    result = subprocess.run(
        ["bash", str(ROOT / "infra/host/operation.sh"), "boxie", "1-1"],
        env={
            **os.environ,
            "CLIS_RELEASE_ROOT": str(tmp_path / "releases"),
            "CLIS_STATE_DIR": str(state),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "changed since workflow preflight" in result.stderr
    assert not (state / "pending").exists()


@pytest.mark.skipif(os.uname().sysname != "Linux", reason="native Linux flock acceptance")
def test_host_lock_refuses_a_second_mutation(tmp_path: Path) -> None:
    import time

    lock = tmp_path / "deployment.lock"
    ready = tmp_path / "ready"
    with subprocess.Popen(
        ["flock", "-n", str(lock), "sh", "-c", 'touch "$1"; sleep 10', "sh", str(ready)]
    ) as first:
        try:
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert ready.exists()
            second = subprocess.run(["flock", "-n", str(lock), "true"], check=False)
            assert second.returncode == 1
        finally:
            first.terminate()
