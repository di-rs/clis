"""Local checks of the smoke's finite execution/privacy boundary, not native proof."""

import importlib
import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest


def test_deadline_reserves_restoration_time() -> None:
    runtime = importlib.import_module("observability_infra.smoke_runtime")
    deadline = runtime.Deadline(0.12, reserve=0.08)
    time.sleep(0.05)
    with pytest.raises(TimeoutError):
        deadline.remaining()
    assert deadline.remaining(restoring=True) > 0


def test_command_timeout_kills_owned_process_group(tmp_path: Path) -> None:
    runtime = importlib.import_module("observability_infra.smoke_runtime")
    pidfile = tmp_path / "pid"
    runner = runtime.Commands(runtime.Deadline(0.6, reserve=0.1))
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        runner.run(
            [
                sys.executable,
                "-c",
                "import os,time,pathlib; pathlib.Path("
                + repr(str(pidfile))
                + ").write_text(str(os.getpid())); time.sleep(20)",
            ]
        )
    assert time.monotonic() - start < 2
    pid = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_command_output_overflow_is_finite_and_does_not_echo_content() -> None:
    runtime = importlib.import_module("observability_infra.smoke_runtime")
    runner = runtime.Commands(runtime.Deadline(2, reserve=0.1), output_limit=1024)
    with pytest.raises(RuntimeError, match="output limit") as raised:
        runner.run([sys.executable, "-c", "print('PRIVATE_SENTINEL' * 10000)"])
    assert "PRIVATE_SENTINEL" not in str(raised.value)


def test_privacy_scans_decoded_records_and_raw_diagnostics() -> None:
    runtime = importlib.import_module("observability_infra.smoke_runtime")
    forbidden = "secret-sentinel"
    escaped = json.dumps({"message": forbidden}).replace("s", "\\u0073")
    with pytest.raises(RuntimeError, match="privacy"):
        runtime.check_privacy(escaped, (forbidden,))
    with pytest.raises(RuntimeError, match="privacy"):
        runtime.check_privacy("diagnostic " + forbidden, (forbidden,))
    runtime.check_privacy('{"event":"request.completed"}', (forbidden,))


def test_watchdog_restores_only_matching_process_identity(tmp_path: Path) -> None:
    if sys.platform != "linux":
        pytest.skip("Linux /proc process identity; native CI exercises actual SIGSTOP restoration")
    watchdog = importlib.import_module("observability_infra.smoke_watchdog")
    import subprocess

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
    try:
        identity = watchdog.process_identity(child.pid)
        os.kill(child.pid, signal.SIGSTOP)
        assert watchdog.resume(child.pid, identity + "-wrong") is False
        assert watchdog.resume(child.pid, identity) is True
    finally:
        child.kill()
        child.wait(timeout=2)


def test_stack_executor_overlay_and_daemon_are_consistent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.syspath_prepend(str(Path("tools/boxie/tests").resolve()))
    from integration.stack import LifecycleStack

    calls: list[tuple[list[str], dict[str, str] | None]] = []

    def execute(args: list[str], *, env: dict[str, str] | None = None) -> str:
        calls.append((args, env))
        return ""

    overlay = tmp_path / "owned.yml"
    stack = LifecycleStack(tmp_path, command_runner=execute, runtime_overlay=overlay)
    stack.env["DOCKER_HOST"] = "unix:///owned/docker.sock"
    stack.runtime_command("config")
    stack.compose("platform", "config")
    assert all(env and env["DOCKER_HOST"] == "unix:///owned/docker.sock" for _, env in calls)
    assert calls[0][0][-3:] == ["--file", str(overlay), "config"]


def test_failed_create_proxy_rejects_before_upstream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    monkeypatch.syspath_prepend(str(Path("tools/boxie/tests").resolve()))
    from integration.faults import Faults

    (tmp_path / "fail_create").touch()
    monkeypatch.setenv("FAULT_DOCKER_SOCKET", str(tmp_path / "nonexistent.sock"))

    async def exercise() -> None:
        proxy = Faults(tmp_path)
        server = await asyncio.start_server(proxy.docker, "127.0.0.1", 0)
        async with server:
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", server.sockets[0].getsockname()[1]
            )
            writer.write(b"POST /v1.47/containers/create HTTP/1.1\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            result = await asyncio.wait_for(reader.read(), 1)
            writer.close()
            await writer.wait_closed()
        assert result.startswith(b"HTTP/1.1 503")
        assert (tmp_path / "failed_create_seen").is_file()

    asyncio.run(exercise())


def test_native_fixture_refuses_development_host_before_creating_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resources = importlib.import_module("observability_infra.smoke_resources")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(RuntimeError, match="ephemeral"):
        resources.require_ephemeral_runner()
    assert list(tmp_path.iterdir()) == []


def test_resource_manifest_rejects_foreign_root(tmp_path: Path) -> None:
    resources = importlib.import_module("observability_infra.smoke_resources")
    with pytest.raises(ValueError, match="owned root"):
        resources.Resources(tmp_path, None)


def test_module_entrypoint_help_is_executable() -> None:
    import subprocess

    result = subprocess.run(
        [sys.executable, "-m", "observability_infra.smoke", "--help"],
        capture_output=True,
        timeout=5,
        env=dict(os.environ, LC_ALL="C", TZ="UTC"),
    )
    assert result.returncode == 0
    assert result.stderr == b""
    assert b"{prepare,scenario,cleanup,export}" in result.stdout
    assert b"--scenario" in result.stdout


def test_independent_watchdog_removes_exact_filler_and_rejects_replacement(tmp_path: Path) -> None:
    from observability_infra.smoke_watchdog import watch

    (tmp_path / "journal").mkdir()
    filler = tmp_path / "journal/filler"
    filler.write_bytes(b"owned")
    manifest = tmp_path / "watch-test.json"
    manifest.write_text(
        json.dumps(
            {
                "restore_at": time.monotonic(),
                "fillers": [{"path": str(filler), "inode": filler.stat().st_ino + 1}],
            }
        )
    )
    watch(manifest)
    assert filler.read_bytes() == b"owned"
    assert json.loads(manifest.with_suffix(".result.json").read_text()) == {"filler_removed": False}
    manifest.write_text(
        json.dumps(
            {
                "restore_at": time.monotonic(),
                "fillers": [{"path": str(filler), "inode": filler.stat().st_ino}],
            }
        )
    )
    watch(manifest)
    assert not filler.exists()
    assert json.loads(manifest.with_suffix(".result.json").read_text()) == {"filler_removed": True}


def test_recovery_identity_comparison_tolerates_duplicates_not_missing_events() -> None:
    runtime = importlib.import_module("observability_infra.smoke_runtime")
    source = Path(__file__).with_name("fixtures") / "events.jsonl"
    assert runtime.event_identities(source.read_text(), "11111111-1111-4111-8111-111111111111") == {
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
    }
    assert (
        runtime.event_identities(source.read_text(), "22222222-2222-4222-8222-222222222222")
        == set()
    )
    with pytest.raises(ValueError):
        runtime.event_identities(
            '{"instance_id":"11111111-1111-4111-8111-111111111111","operation_id":"bad"}',
            "11111111-1111-4111-8111-111111111111",
        )


@pytest.mark.integration
def test_smoke_overlay_renders_real_journal_and_owned_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    from observability_infra.smoke_application import application_overlay

    monkeypatch.syspath_prepend(str(Path("tools/boxie/tests").resolve()))
    from integration.stack import LifecycleStack

    application_overlay(tmp_path)
    stack = LifecycleStack(
        tmp_path / "application", runtime_overlay=tmp_path / "application-overlay.yml"
    )
    stack.env.update(BOXIE_TEST_API_PORT="18090", BOXIE_TEST_PRODUCT_PORT="18000")
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            stack.runtime,
            "--file",
            "tools/boxie/infra/compose.test.yml",
            "--file",
            str(stack.runtime_overlay),
            "config",
            "--format",
            "json",
        ],
        env=stack.env,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert result.stderr == b""
    services = json.loads(result.stdout)["services"]
    for role in ("api", "worker", "agent"):
        assert services[role]["logging"] == {
            "driver": "journald",
            "options": {"mode": "non-blocking", "max-buffer-size": "4m", "tag": "boxie-" + role},
        }
    socket = [
        item for item in services["faults"]["volumes"] if item["target"] == "/var/run/docker.sock"
    ]
    assert len(socket) == 1
    assert socket[0]["source"] == str(tmp_path / "docker.sock")
