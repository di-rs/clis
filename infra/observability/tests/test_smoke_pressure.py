"""Actual buffer pressure and finite flood observations survive failed assertions."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from observability_infra.smoke_scenarios import Scenario


def pressure_scenario(tmp_path: Path) -> Scenario:
    scenario = object.__new__(Scenario)
    scenario.root = tmp_path
    scenario.name = "vector-full"
    scenario.flood_instance = str(UUID(int=5))
    scenario.flood_id = "a" * 64
    scenario.observations = {}
    (tmp_path / "control").mkdir()
    info = [
        {
            "Id": scenario.flood_id,
            "Name": "/" + tmp_path.name + "-flood-vector-full",
            "Config": {"Labels": {"io.boxie.test.owner": tmp_path.name}},
            "State": {"Running": False, "Status": "exited", "ExitCode": 0, "OOMKilled": False},
        }
    ]
    scenario.command = AsyncMock(return_value=json.dumps(info))  # type: ignore[method-assign]
    return scenario


def metrics(size: float, dropped: float) -> dict[str, list[dict[str, object]]]:
    return {
        name: [{"labels": {"component_id": "storage"}, "value": value}]
        for name, value in (
            ("vector_buffer_size_bytes", size),
            ("vector_buffer_discarded_events_total", dropped),
        )
    }


def test_current_peak_emission_and_exact_flood_exit_are_bounded(tmp_path: Path) -> None:
    scenario = pressure_scenario(tmp_path)
    (tmp_path / "control/emitted.json").write_text(
        json.dumps({"instance": scenario.flood_instance, "emitted": 1000000})
    )
    for index in range(100):
        asyncio.run(
            scenario.buffer_pressure(
                metrics(1000 if index == 20 else 100, 5 if index >= 20 else 2), 2
            )
        )
    observation = scenario.observations["vector_pressure"]
    assert observation["samples"] == 100
    assert observation["peak_buffer_bytes"] == 1000
    assert observation["peak_discarded_delta"] == 3
    assert observation["latest"]["emitted_lower_bound"] == 1000000
    assert observation["latest"]["flood_state"] == {
        "running": False,
        "status": "exited",
        "exit_code": 0,
        "oom_killed": False,
    }
    assert len(json.dumps(observation)) < 2000


@pytest.mark.parametrize(
    "fault", ["foreign", "malformed_emission", "oversized_emission", "exited_error"]
)
def test_pressure_diagnostics_reject_invalid_owned_state(tmp_path: Path, fault: str) -> None:
    scenario = pressure_scenario(tmp_path)
    if fault == "foreign":
        scenario.command = AsyncMock(return_value=json.dumps([{"Id": "b" * 64}]))  # type: ignore[method-assign]
    elif fault == "malformed_emission":
        (tmp_path / "control/emitted.json").write_text(
            json.dumps({"instance": scenario.flood_instance, "emitted": -1})
        )
    elif fault == "oversized_emission":
        (tmp_path / "control/emitted.json").write_text("x" * 4097)
    else:
        command = scenario.command
        assert isinstance(command, AsyncMock)
        info = json.loads(command.return_value)
        info[0]["State"]["ExitCode"] = 1
        command.return_value = json.dumps(info)
    with pytest.raises(RuntimeError):
        asyncio.run(scenario.buffer_pressure(metrics(100, 0), 0))
    if fault == "exited_error":
        assert scenario.observations["vector_pressure"]["latest"]["flood_state"]["exit_code"] == 1


def test_missing_or_prior_flood_emission_is_not_invented_as_zero(tmp_path: Path) -> None:
    scenario = pressure_scenario(tmp_path)
    asyncio.run(scenario.buffer_pressure(metrics(100, 0), 0))
    assert scenario.observations["vector_pressure"]["latest"]["emitted_lower_bound"] is None
    (tmp_path / "control/emitted.json").write_text(json.dumps({"instance": "old", "emitted": 900}))
    asyncio.run(scenario.buffer_pressure(metrics(100, 0), 0))
    assert scenario.observations["vector_pressure"]["latest"]["emitted_lower_bound"] is None
