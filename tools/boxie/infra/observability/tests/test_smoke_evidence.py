"""Failure evidence remains useful even when outer resource cleanup stalls."""

import json
from pathlib import Path

import pytest
import yaml


def test_precleanup_export_is_allowlisted_and_records_absent_scenario_report(
    tmp_path: Path,
) -> None:
    from observability_infra.smoke import export_evidence, record_step

    root, destination = tmp_path / "root", tmp_path / "out"
    root.mkdir()
    (root / "application-private.json").write_text('{"secret":"PRIVATE_CREDENTIAL"}')
    (root / "daemon.json").write_text("PRIVATE_CREDENTIAL")
    (root / "runtime.log").write_text("PRIVATE_CREDENTIAL")
    record_step(root, "lifecycle-failure", 2)
    export_evidence(root, destination, setup_outcome="success", scenario_outcome="failure")
    assert {path.name for path in destination.iterdir()} == {
        "scenario-step.json",
        "workflow-step.json",
    }
    data = json.loads((destination / "scenario-step.json").read_text())
    assert data == {"scenario": "lifecycle-failure", "exit_code": 2, "report_present": False}
    assert "PRIVATE_CREDENTIAL" not in "".join(path.read_text() for path in destination.iterdir())
    with pytest.raises(ValueError):
        record_step(root, "arbitrary-private-text", 1)


def test_workflow_uploads_distinct_evidence_before_and_after_cleanup() -> None:
    workflow = yaml.safe_load(Path(".github/workflows/boxie-observability.yml").read_text())
    steps = workflow["jobs"]["native"]["steps"]
    index = {step.get("id"): number for number, step in enumerate(steps) if step.get("id")}
    assert (
        index["scenarios"]
        < index["evidence_before_cleanup"]
        < index["upload_before_cleanup"]
        < index["cleanup"]
        < index["upload_after_cleanup"]
    )
    before, after = steps[index["upload_before_cleanup"]], steps[index["upload_after_cleanup"]]
    assert before["with"]["name"] != after["with"]["name"]
    assert before["with"]["path"].endswith("/*.json")
    assert "always()" in before["if"]
    assert "record-step" in steps[index["scenarios"]]["run"]


def test_cleanup_phase_is_only_a_checkpoint_and_rejects_arbitrary_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from observability_infra.smoke_runtime import cleanup_phase

    cleanup_phase(tmp_path, "resources")
    assert json.loads((tmp_path / "cleanup-progress.json").read_text()) == {
        "phase": "resources",
        "status": "in_progress",
    }
    assert json.loads(capsys.readouterr().out) == {"cleanup_phase": "resources"}
    with pytest.raises(ValueError):
        cleanup_phase(tmp_path, "PRIVATE_CREDENTIAL")


@pytest.mark.parametrize("inside", [True, False])
def test_preexercise_failure_retains_only_error_type_and_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, inside: bool
) -> None:
    import sys

    from observability_infra import smoke

    secret = "PRIVATE_ERROR_CONTENT"

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError(secret)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "smoke",
            "scenario",
            "--root",
            str(tmp_path),
            "--scenario",
            "lifecycle-failure",
            *(["--inside-unit"] if inside else []),
        ],
    )
    monkeypatch.setattr(smoke, "run_smoke", fail)
    monkeypatch.setattr(smoke, "require_ephemeral_runner", lambda: None)
    monkeypatch.setattr(smoke, "transient_command", fail)
    with pytest.raises(SystemExit):
        smoke.main()
    output = (tmp_path / "scenario-lifecycle-failure.json").read_text()
    assert secret not in output
    data = json.loads(output)
    assert data["stage"] == ("initialization" if inside else "launcher")
    assert data["error_type"] == "RuntimeError"
    assert data["failure_location"][-1]["function"] == "fail"
