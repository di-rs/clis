"""Malformed journal observations remain fatal without exporting their payload."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from observability_infra import smoke_runtime
from observability_infra.smoke_scenarios import Scenario


@pytest.mark.parametrize(
    ("line", "category", "position"),
    [
        ('{"secret":"private-value"', "delimiter", 25),
        ("private-value", "value", 0),
        ('{"secret":"private-value\x00"}', "control_character", 24),
        ("Failed to iterate through journal: Bad message", "value", 0),
    ],
)
def test_parse_failure_retains_only_fixed_metadata(line: str, category: str, position: int) -> None:
    with pytest.raises(smoke_runtime.EventIdentityError) as caught:
        smoke_runtime.event_identities(line, "private-instance")
    detail = caught.value.detail
    assert detail["category"] == category
    assert detail["position"] == position
    assert detail["line_bytes"] == len(line.encode())
    assert detail["line_number"] == 1
    assert "private" not in json.dumps(detail)
    assert "private" not in str(caught.value)
    if line.startswith("Failed"):
        assert detail["journal_diagnostic"] == {"stage": "iteration", "reason": "bad_message"}


@pytest.mark.parametrize(
    "line",
    ["[]", "null", '{"instance_id":"probe"}', '{"instance_id":"probe","operation_id":"private"}'],
)
def test_invalid_event_shape_or_identity_cannot_pass(line: str) -> None:
    with pytest.raises(smoke_runtime.EventIdentityError) as caught:
        smoke_runtime.event_identities(line, "probe")
    assert caught.value.detail["category"] in {"event_shape", "operation_identity"}
    assert "private" not in json.dumps(caught.value.detail)


def test_accepted_retains_surface_and_merged_provenance(tmp_path: Path) -> None:
    scenario = object.__new__(Scenario)
    scenario.root = tmp_path
    scenario.flood_instance = "probe"
    scenario.observations = {}
    scenario.command = AsyncMock(return_value="private diagnostic")  # type: ignore[method-assign]
    with pytest.raises(smoke_runtime.EventIdentityError):
        asyncio.run(scenario.accepted())
    detail = scenario.observations["journal_parse_failure"]
    assert detail["surface"] == "persistent"
    assert detail["output_provenance"] == "stdout_stderr_merged"
    assert "private" not in json.dumps(detail)


def test_runtime_observation_retains_surface_before_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario = object.__new__(Scenario)
    scenario.root = tmp_path
    scenario.probe_instance = "probe"
    scenario.observations = {}
    (tmp_path / "ownership.json").write_text(
        json.dumps({"journal_path": "/journal/machine." + tmp_path.name})
    )
    (tmp_path / "test.journal").touch()
    monkeypatch.setattr(Path, "glob", lambda *args: iter([tmp_path / "test.journal"]))
    scenario.command = AsyncMock(side_effect=["", "private diagnostic"])  # type: ignore[method-assign]
    with pytest.raises(smoke_runtime.EventIdentityError):
        asyncio.run(scenario.journal_observation())
    assert scenario.observations["journal_parse_failure"]["surface"] == "runtime"
