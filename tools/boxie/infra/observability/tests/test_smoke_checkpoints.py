"""Nonfinal ENOSPC evidence survives restoration and retains the report reserve."""

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from observability_infra import smoke, smoke_runtime
from observability_infra.smoke_scenarios import Scenario


def test_report_reserve_preserves_exercise_and_bounds_restoration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(time, "monotonic", lambda: 100.0)
    deadline = smoke_runtime.Deadline(180, reserve=45, report_reserve=5)
    assert deadline.remaining() == 135
    assert deadline.remaining(restoring=True) == 175
    monkeypatch.setattr(time, "monotonic", lambda: 236.0)
    with pytest.raises(TimeoutError):
        deadline.remaining()
    assert deadline.remaining(restoring=True) == 39
    monkeypatch.setattr(time, "monotonic", lambda: 275.0)
    with pytest.raises(TimeoutError):
        deadline.remaining(restoring=True)
    assert deadline.end == 280


def test_checkpoint_has_fixed_phases_safe_selection_and_bounded_export(tmp_path: Path) -> None:
    scenario = object.__new__(Scenario)
    scenario.root, scenario.name = tmp_path, "vector-enospc"
    scenario.commands = smoke_runtime.Commands(smoke_runtime.Deadline(10, reserve=2))
    scenario.observations = {
        "enospc_observed": True,
        "vector_fault_trigger": "disk_writer_failure",
        "vector_enospc_diagnostic_observed": False,
        "private": "PRIVATE",
        "resources": {"private": "PRIVATE"},
    }
    scenario.enospc_checkpoint("allocation_completed")
    path = tmp_path / "enospc-checkpoint-vector-enospc.json"
    original = path.read_text()
    assert "PRIVATE" not in original
    assert json.loads(original)["status"] == "in_progress"
    assert json.loads(original)["vector_fault_trigger"] == "disk_writer_failure"
    assert json.loads(original)["vector_enospc_diagnostic_observed"] is False
    with pytest.raises(ValueError):
        scenario.enospc_checkpoint("PRIVATE")
    scenario.observations["progress"] = {"count": "x" * 20000}
    with pytest.raises(RuntimeError):
        scenario.enospc_checkpoint("diagnostic_wait")
    assert path.read_text() == original
    (tmp_path / "enospc-checkpoint-PRIVATE.json").write_text("PRIVATE")
    destination = tmp_path / "export"
    smoke.export_evidence(tmp_path, destination)
    assert (destination / path.name).read_text() == original
    assert not (destination / "enospc-checkpoint-PRIVATE.json").exists()


@pytest.mark.parametrize("checkpoint_failure", [False, True])
def test_exercise_error_checkpoint_precedes_restoration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, checkpoint_failure: bool
) -> None:
    operations = []

    class App:
        def __init__(self, root: Path, commands: smoke_runtime.Commands) -> None:
            self.root, self.commands, self.stack = root, commands, None

        async def close(self) -> None:
            operations.append("close")

    class Case(Scenario):
        def enospc_checkpoint(self, phase: str) -> None:
            if checkpoint_failure:
                raise OSError("PRIVATE")
            super().enospc_checkpoint(phase)

        def __init__(self, name: str, app: App, telemetry: Any) -> None:
            self.name, self.root, self.commands = name, app.root, app.commands
            self.observations: dict[str, Any] = {}

        async def run(self) -> None:
            self.enospc_checkpoint("diagnostic_wait")
            raise RuntimeError("PRIVATE")

        async def restore(self) -> None:
            operations.append("restore")
            if checkpoint_failure:
                return
            saved = (self.root / "enospc-checkpoint-vector-enospc.json").read_text()
            assert "PRIVATE" not in saved
            assert json.loads(saved)["exercise_errors"] == ["RuntimeError"]
            assert json.loads(saved)["fault_phase"] == "diagnostic_wait"

    async def metrics(service: str) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(smoke, "require_ephemeral_runner", lambda: None)
    monkeypatch.setattr(smoke, "unit_memory", lambda *a, **kw: {})
    monkeypatch.setattr(smoke, "Application", App)
    monkeypatch.setattr(smoke, "Telemetry", lambda *a: SimpleNamespace(metrics=metrics))
    monkeypatch.setattr(smoke, "Scenario", Case)
    report = smoke.run_smoke(smoke.SmokeConfig(tmp_path, "vector-enospc"), 3)
    assert report.status == "failed" and report.cleanup_errors == ()
    assert operations == ["restore", "close"]
    if checkpoint_failure:
        assert "checkpoint_OSError" in report.observations["errors"]
        assert "PRIVATE" not in (tmp_path / "scenario-vector-enospc.json").read_text()
