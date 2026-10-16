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


def test_failed_metric_waits_through_cached_zero() -> None:
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from observability_infra.smoke_scenarios import Scenario

    query = AsyncMock(
        side_effect=[
            json.dumps({"data": {"result": [{"value": [1, "0"]}]}}),
            json.dumps({"data": {"result": [{"value": [2, "1"]}]}}),
        ]
    )
    scenario = object.__new__(Scenario)
    scenario.telemetry = SimpleNamespace(query=query)  # type: ignore[assignment]
    asyncio.run(scenario.wait_failed_metric(1))
    assert query.await_count == 2
    assert "timestamp(" in query.call_args.kwargs["params"]["query"]


def test_terminal_privacy_requires_exercised_marker_before_inspection() -> None:
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from observability_infra.smoke_scenarios import Scenario

    order: list[str] = []

    async def progress(sandbox: str) -> dict[str, bool]:
        order.append("terminal")
        return {"terminal_marker_observed": True}

    async def inspect(*args: object) -> dict[str, int]:
        assert order == ["terminal"]
        return {"decoded_records": 1}

    scenario = object.__new__(Scenario)
    scenario.app = SimpleNamespace(
        provision=AsyncMock(return_value="success"), progress=progress, sentinels=("private",)
    )  # type: ignore[assignment]
    scenario.telemetry = SimpleNamespace(wait_logs=AsyncMock(), inspect_privacy=inspect)  # type: ignore[assignment]
    scenario.observations = {}
    asyncio.run(scenario.terminal_privacy())
    assert scenario.observations["terminal_privacy"]["exercised"] is True


def test_cgroup_resolution_waits_for_namespace_activation_and_rejects_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from observability_infra.smoke_resources import Resources
    from observability_infra.smoke_runtime import Commands, Deadline

    resources = Resources(
        Path("/var/tmp/boxie-observability-0123456789ab"), Commands(Deadline(2, reserve=0.1))
    )
    unit = resources.journal_unit
    values = iter(["", f"/system.slice/{unit}"])

    def command(args: list[str], **kwargs: object) -> str:
        if "--property=ControlGroup" in args:
            return next(values)
        return "1234"

    monkeypatch.setattr(resources.commands, "run", command)
    original = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda path, *a, **kw: (
            f"0::/system.slice/{unit}\n"
            if str(path) == "/proc/1234/cgroup"
            else original(path, *a, **kw)
        ),
    )
    assert resources.owned_cgroup(unit) == f"/system.slice/{unit}"
    monkeypatch.setattr(resources.commands, "run", lambda *a, **kw: "/")
    with pytest.raises(RuntimeError, match="cgroup"):
        resources.owned_cgroup(unit)


def test_restoration_fallback_rejects_false_and_missing_results(tmp_path: Path) -> None:
    from observability_infra.smoke_runtime import Commands, Deadline
    from observability_infra.smoke_watchdog import restore_registered

    (tmp_path / "journal").mkdir()
    filler = tmp_path / "journal/filler"
    filler.write_text("foreign replacement")
    manifest = tmp_path / "watch-journal-enospc.json"
    manifest.write_text(
        json.dumps({"fillers": [{"path": str(filler), "inode": filler.stat().st_ino + 1}]})
    )
    for previous in (None, {"filler_removed": False}):
        if previous is not None:
            manifest.with_suffix(".result.json").write_text(json.dumps(previous))
        result = restore_registered(manifest, Commands(Deadline(2, reserve=0.1)), wait_seconds=0)
        assert result["restored"] is False
        assert result["fallback"]["filler_removed"] is False
        assert filler.read_text() == "foreign replacement"


def test_sequence_observation_reports_missing_and_duplicates_without_exactly_once() -> None:
    from observability_infra.smoke_scenarios import sequence_observation

    one = "00000000-0000-0000-0000-000000000001"
    two = "00000000-0000-0000-0000-000000000002"
    observed = sequence_observation(3, {one, two}, [one, one])
    assert observed["emitted_not_observed_in_journal"] == 1
    assert observed["accepted_not_observed_in_storage"] == 1
    assert observed["stored_duplicates"] == 1
    assert observed["exactly_once"] is False


@pytest.mark.parametrize("prior", [None, {"filler_removed": False}])
def test_outer_cleanup_never_tears_down_an_unrestored_fault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prior: dict[str, bool] | None
) -> None:
    from types import SimpleNamespace
    from unittest.mock import Mock

    from observability_infra import smoke
    from observability_infra.smoke_watchdog import restore_registered

    (tmp_path / "ownership.json").write_text("{}")
    (tmp_path / "journal").mkdir()
    filler = tmp_path / "journal/filler"
    filler.write_text("replacement")
    manifest = tmp_path / "watch-journal-enospc.json"
    manifest.write_text(json.dumps({"fillers": [{"path": str(filler), "inode": -1}]}))
    if prior is not None:
        manifest.with_suffix(".result.json").write_text(json.dumps(prior))
    close = Mock()
    monkeypatch.setattr(smoke, "require_ephemeral_runner", lambda: None)
    monkeypatch.setattr(
        smoke, "Resources", lambda *args: SimpleNamespace(state={"mounts": {}}, close=close)
    )
    monkeypatch.setattr(
        smoke,
        "restore_registered",
        lambda path, commands: restore_registered(path, commands, wait_seconds=0),
    )
    with pytest.raises(RuntimeError, match="teardown withheld"):
        smoke.cleanup(tmp_path)
    close.assert_not_called()
    report = json.loads((tmp_path / "cleanup.json").read_text())
    assert report["restorations"][manifest.name]["restored"] is False
    assert filler.exists()


def test_outer_cleanup_preserves_resource_failure_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from observability_infra import smoke

    (tmp_path / "ownership.json").write_text("{}")

    def close() -> None:
        (tmp_path / "cleanup.json").write_text(
            json.dumps({"errors": ["owned-loop-busy"], "mounts_remaining": ["owned-mount"]})
        )
        raise RuntimeError("cleanup failed")

    monkeypatch.setattr(smoke, "require_ephemeral_runner", lambda: None)
    monkeypatch.setattr(smoke, "Resources", lambda *args: SimpleNamespace(close=close))
    with pytest.raises(RuntimeError, match="cleanup incomplete"):
        smoke.cleanup(tmp_path)
    report = json.loads((tmp_path / "cleanup.json").read_text())
    assert report["errors"] == ["owned-loop-busy"]
    assert report["mounts_remaining"] == ["owned-mount"]


def test_failed_command_diagnostic_is_bounded_and_does_not_disclose_output() -> None:
    from observability_infra.smoke_runtime import Commands, Deadline

    runner = Commands(Deadline(2, reserve=0.1))
    with pytest.raises(RuntimeError):
        runner.run([sys.executable, "-c", "print('SECRET_SENTINEL manifest unknown'); exit(7)"])
    detail = runner.last_failure
    assert detail is not None
    assert detail["returncode"] == 7
    assert detail["classification"] == ["image_manifest"]
    assert "SECRET_SENTINEL" not in json.dumps(detail)
    assert "-c" not in json.dumps(detail)


def test_setup_diagnostics_exports_only_allowlisted_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from observability_infra import smoke_diagnostics

    secret = "PRIVATE_ENV_HEALTH_OUTPUT"

    def run(self: object, args: list[str], **kwargs: object) -> str:
        if args[1] == "info":
            return json.dumps(
                {
                    "DockerRootDir": str(tmp_path / "docker-data"),
                    "Labels": ["io.boxie.test.owner=" + tmp_path.name],
                }
            )
        if args[1] == "version" or args[1:3] == ["compose", "version"]:
            return "29.0.0"
        if args[1] == "ps":
            return "a" * 64
        if args[1] == "inspect":
            return json.dumps(
                [
                    {
                        "Id": "a" * 64,
                        "Image": "sha256:" + "b" * 64,
                        "Config": {"Env": [secret]},
                        "State": {
                            "Status": "exited",
                            "ExitCode": 4,
                            "OOMKilled": False,
                            "Health": {"Status": "unhealthy", "Output": secret},
                        },
                    }
                ]
            )
        return ""

    monkeypatch.setattr("observability_infra.smoke_diagnostics.Commands.run", run)
    stack = SimpleNamespace(
        env={}, platform="owned-platform", temporal="owned-temporal", runtime="owned-runtime"
    )
    smoke_diagnostics.setup_diagnostics(tmp_path, stack, {"returncode": 1})
    output = (tmp_path / "setup-diagnostics.json").read_text()
    assert secret not in output
    assert json.loads(output)["containers"][0]["exit_code"] == 4


def test_journal_observation_separates_runtime_and_persistent_acceptance(tmp_path: Path) -> None:
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from observability_infra.smoke_scenarios import Scenario

    (tmp_path / "journal").mkdir()
    (tmp_path / "journal/sample.journal").write_bytes(
        b"binary placeholder for filesystem stats only"
    )
    (tmp_path / "control").mkdir()
    (tmp_path / "ownership.json").write_text(
        json.dumps({"journal_path": "/owned/machine." + tmp_path.name})
    )
    instance = "11111111-1111-4111-8111-111111111111"
    one = "00000000-0000-0000-0000-000000000001"
    (tmp_path / "control/emitted.json").write_text(
        json.dumps({"instance": "pressure", "emitted": 1000, "probe_emitted": 3})
    )
    scenario = object.__new__(Scenario)
    scenario.root = tmp_path
    scenario.probe_instance = instance
    scenario.flood_instance = "pressure"
    scenario.command = AsyncMock(  # type: ignore[method-assign]
        return_value=json.dumps({"instance_id": instance, "operation_id": one})
    )
    scenario.telemetry = SimpleNamespace(
        logs=AsyncMock(return_value=[{"operation_id": one}, {"operation_id": one}])
    )  # type: ignore[assignment]
    report = asyncio.run(scenario.journal_observation())
    assert report["persistent"]["accepted_probe"] == [one]
    assert report["runtime"]["files"] == 0
    assert report["sequence"]["emitted_not_observed_in_journal"] == 2
    assert report["sequence"]["stored_duplicates"] == 1
    assert report["pressure_emitted_lower_bound"] == 1000


@pytest.mark.parametrize(
    "output,code,empty",
    [
        ("-- No entries --", 1, True),
        ("Permission denied", 1, False),
        ("-- No entries --", 2, False),
    ],
)
def test_empty_journal_query_does_not_hide_read_errors(
    tmp_path: Path, output: str, code: int, empty: bool
) -> None:
    from observability_infra.smoke_runtime import Commands, Deadline

    executable = tmp_path / "journalctl"
    executable.write_text("#!/bin/sh\nprintf '%s\\n' '" + output + "'\nexit " + str(code) + "\n")
    executable.chmod(0o700)
    runner = Commands(Deadline(2, reserve=0.1))
    args = [str(executable), "--grep=probe"]
    if empty:
        assert runner.run(args, empty_journal_ok=True) == ""
    else:
        with pytest.raises(RuntimeError):
            runner.run(args, empty_journal_ok=True)
