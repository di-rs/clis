"""Real bounded diagnostic subprocesses and exact restart identity contracts."""

import asyncio
import json
import os
import sys
import time
from contextlib import nullcontext
from pathlib import Path

import pytest
from observability_infra.smoke_runtime import Deadline


def wait_for(reader: object, key: str) -> dict[str, object]:
    until = time.monotonic() + 3
    while time.monotonic() < until:
        result = reader.snapshot()  # type: ignore[attr-defined]
        if result[key]:
            return result  # type: ignore[no-any-return]
        time.sleep(0.01)
    raise AssertionError("reader observation missing")


def test_follow_cumulates_split_error_without_exporting_payload_and_reaps() -> None:
    from observability_infra.smoke_vector_fault import VectorDiagnostics

    code = "import os,time; os.write(1,b'PRIVATE No space left'); time.sleep(.03); os.write(1,b' on device\\n'); time.sleep(30)"
    reader = VectorDiagnostics([sys.executable, "-c", code], {}, Deadline(5, reserve=1))
    with reader:
        observed = wait_for(reader, "errno_text_observed")
        assert "PRIVATE" not in json.dumps(observed)
        assert reader.snapshot()["errno_text_observed"] is True
        pid = reader.process.pid
    assert reader.snapshot()["reaped"] is True
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.parametrize(
    "code",
    [
        "import os; os.write(1,b'x'*65537)",
        "import os; os.write(1,b'x\\n'*4097)",
        "import os; [os.write(1,b'x'*65535+b'\\n') for _ in range(5)]",
        "raise SystemExit(2)",
    ],
)
def test_follow_bounds_and_command_failure_are_fatal_and_reaped(code: str) -> None:
    from observability_infra.smoke_vector_fault import VectorDiagnostics

    reader = VectorDiagnostics([sys.executable, "-c", code], {}, Deadline(5, reserve=1))
    with pytest.raises(RuntimeError, match="diagnostic reader"), reader:
        wait_for(reader, "error_type")
        with pytest.raises(RuntimeError, match="diagnostic reader"):
            reader.check()
    assert reader.snapshot()["reaped"] is True


def test_follow_deadline_and_cancellation_reap() -> None:
    from observability_infra.smoke_vector_fault import VectorDiagnostics

    reader = VectorDiagnostics(
        [sys.executable, "-c", "import time; time.sleep(30)"], {}, Deadline(0.3, reserve=0.1)
    )
    with pytest.raises(RuntimeError, match="diagnostic reader"), reader:
        assert wait_for(reader, "error_type")["error_type"] == "TimeoutError"
    assert reader.snapshot()["reaped"] is True
    reader = VectorDiagnostics(
        [sys.executable, "-c", "import time; time.sleep(30)"], {}, Deadline(5, reserve=1)
    )
    with pytest.raises(KeyboardInterrupt), reader:
        raise KeyboardInterrupt
    assert reader.snapshot()["reaped"] is True


def test_restart_refresh_preserves_baseline_and_rejects_changed_identity() -> None:
    import asyncio
    from types import SimpleNamespace
    from typing import Any

    from observability_infra.smoke_scenarios import Scenario

    state: dict[str, Any] = {
        "Id": "a" * 64,
        "Pid": 10,
        "StartedAt": "2026-10-16T00:00:00Z",
        "RestartCount": 2,
        "Running": True,
        "Restarting": False,
        "ExitCode": 0,
        "OOMKilled": False,
    }
    refreshed = []
    endpoints = {"vector": "http://172.20.0.2:9598"}

    def identity(expected: str | None) -> dict[str, Any]:
        if expected is not None and expected != state["Id"]:
            raise RuntimeError("foreign")
        return dict(state)

    def refresh(service: str, *, expected: str) -> None:
        assert expected == "a" * 64 and service == "vector"
        refreshed.append(state["Pid"])
        endpoints[service] = "http://172.20.0.4:9598"

    scenario = object.__new__(Scenario)
    scenario.observations = {}
    scenario.telemetry = SimpleNamespace(  # type: ignore[assignment]
        vector_identity=identity,
        refresh_endpoint=refresh,
        endpoints=endpoints,
    )
    assert asyncio.run(scenario.vector_lifecycle()) is True
    state.update(Pid=20, RestartCount=3, StartedAt="2026-10-16T00:01:00Z")
    assert asyncio.run(scenario.vector_lifecycle()) is True
    record = scenario.observations["vector_fault_lifecycle"]
    assert record["baseline"]["Pid"] == 10
    assert record["restart_count_delta"] == 1 and record["endpoint_changes"] == 1
    assert scenario.observations["vector_fault_state"]["Pid"] == 20
    state.update(Restarting=True, Pid=0)
    assert asyncio.run(scenario.vector_lifecycle()) is False
    assert refreshed == [10, 20]
    state["Id"] = "b" * 64
    with pytest.raises(RuntimeError, match="foreign"):
        asyncio.run(scenario.vector_lifecycle())


def test_follower_scope_reaps_when_fault_is_cancelled() -> None:
    import asyncio
    from types import SimpleNamespace
    from typing import Any

    from observability_infra import smoke_scenarios
    from observability_infra.smoke_vector_fault import VectorDiagnostics

    # Exercise the scenario's actual finally, not merely the reader context manager.
    class Case(smoke_scenarios.Scenario):
        async def vector_lifecycle(self) -> bool:
            self.observations["vector_fault_lifecycle"] = {"baseline": {"Id": "a" * 64}}
            return True

        async def exhaust_filesystem(
            self, sandbox: str, reader: VectorDiagnostics | None = None
        ) -> None:
            assert reader is not None
            raise asyncio.CancelledError

    original = VectorDiagnostics
    created = []

    def factory(*args: Any) -> VectorDiagnostics:
        value = original(
            [sys.executable, "-c", "import time; time.sleep(30)"], {}, Deadline(5, reserve=1)
        )
        created.append(value)
        return value

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(smoke_scenarios, "VectorDiagnostics", factory)
        case = object.__new__(Case)
        case.name, case.observations = "vector-enospc", {}
        case.telemetry = SimpleNamespace(env={}, diagnostic_args=lambda *a, **kw: [])  # type: ignore[assignment]
        case.commands = SimpleNamespace(deadline=Deadline(5, reserve=1))  # type: ignore[assignment]
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(case.exhaustion("sandbox"))
    assert created[0].snapshot()["reaped"] is True
    assert case.observations["vector_fault_diagnostics"]["reaped"] is True


@pytest.mark.parametrize("cancelled", [False, True])
def test_closed_streams_live_child_is_reaped_on_context_exit(
    tmp_path: Path, cancelled: bool
) -> None:
    from observability_infra.smoke_vector_fault import VectorDiagnostics

    ready = tmp_path / "ready"
    code = (
        "import os,sys,time; from pathlib import Path; "
        "os.close(1); os.close(2); Path(sys.argv[1]).touch(); time.sleep(30)"
    )
    reader = VectorDiagnostics([sys.executable, "-c", code, str(ready)], {}, Deadline(5, reserve=1))
    try:
        with pytest.raises(asyncio.CancelledError) if cancelled else nullcontext():
            with reader:
                until = time.monotonic() + 1
                while not ready.exists():
                    assert time.monotonic() < until
                    time.sleep(0.01)
                # Let the selector observe EOF and enter the exited-stream child wait.
                time.sleep(0.1)
                closing = time.monotonic()
                if cancelled:
                    raise asyncio.CancelledError
        assert time.monotonic() - closing < 1
        assert reader.snapshot()["reaped"] is True
        assert not reader.thread.is_alive()
        with pytest.raises(ProcessLookupError):
            os.kill(reader.process.pid, 0)
    finally:
        # Keep the RED test bounded and do not leave its failing reader behind.
        reader.thread.join(timeout=5)


def test_reader_stderr_never_becomes_fault_proof() -> None:
    from observability_infra.smoke_vector_fault import VectorDiagnostics

    reader = VectorDiagnostics(
        [
            sys.executable,
            "-c",
            "import os,time; os.write(2,b'PRIVATE No space left on device\\n'); time.sleep(30)",
        ],
        {},
        Deadline(5, reserve=1),
    )
    with pytest.raises(RuntimeError, match="diagnostic reader"), reader:
        result = wait_for(reader, "error_type")
        assert result["errno_text_observed"] is False
        assert isinstance(result["stderr_bytes"], int) and result["stderr_bytes"] > 0
        assert "PRIVATE" not in json.dumps(result)
    assert reader.snapshot()["reaped"] is True


@pytest.mark.parametrize(
    "enospc,literal,writer,error,stderr,expected",
    [
        (True, True, False, None, 0, "literal_errno"),
        (True, False, True, None, 0, "disk_writer_failure"),
        (True, True, True, None, 0, "literal_errno"),
        (False, True, True, None, 0, None),
        (True, False, False, None, 0, None),
        (True, False, True, "RuntimeError", 0, None),
        (True, True, True, None, 1, None),
    ],
)
def test_vector_fault_trigger_requires_real_exhaustion_and_valid_writer_evidence(
    enospc: bool,
    literal: bool,
    writer: bool,
    error: str | None,
    stderr: int,
    expected: str | None,
) -> None:
    from observability_infra.smoke_scenarios import Scenario

    scenario = object.__new__(Scenario)
    scenario.observations = {"enospc_observed": enospc}
    diagnostic = {
        "errno_text_observed": literal,
        "writer_unrecoverable_observed": writer,
        "error_type": error,
        "stderr_bytes": stderr,
    }
    assert scenario.record_vector_fault(diagnostic) is (expected is not None)
    if expected is None:
        assert "vector_fault_trigger" not in scenario.observations
    else:
        assert scenario.observations["vector_fault_trigger"] == expected
        assert scenario.observations["vector_enospc_diagnostic_observed"] is literal
        # Generic disk failure must never manufacture a literal errno observation.
        assert diagnostic["errno_text_observed"] is literal
