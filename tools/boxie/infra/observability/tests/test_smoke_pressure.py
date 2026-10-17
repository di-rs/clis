"""Actual buffer pressure and finite flood observations survive failed assertions."""

import asyncio
import json
from pathlib import Path
from typing import Any
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
    disk = {"component_id": "storage", "buffer_id": "storage", "buffer_type": "disk", "stage": "0"}
    tail = dict(disk, buffer_type="disk", stage="1", intentional="true")
    return {
        "vector_buffer_size_bytes": [{"labels": disk, "value": size}],
        "vector_buffer_max_size_bytes": [{"labels": disk, "value": 268435488}],
        "vector_buffer_discarded_events_total": [{"labels": tail, "value": dropped}],
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
    assert len(json.dumps(observation)) < 3000


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


def test_missing_stage_drop_and_capacity_are_not_invented(tmp_path: Path) -> None:
    scenario = pressure_scenario(tmp_path)
    sample = metrics(134217288, 0)
    sample.pop("vector_buffer_discarded_events_total")
    asyncio.run(scenario.buffer_pressure(sample, None))
    latest = scenario.observations["vector_pressure"]["latest"]
    assert latest["discarded_delta"] is None
    assert latest["memory_tail_intentional_discarded"] is None
    assert latest["usable_disk_bytes"] == 134217760
    sample.pop("vector_buffer_max_size_bytes")
    with pytest.raises(RuntimeError, match="capacity"):
        asyncio.run(scenario.buffer_pressure(sample, None))


@pytest.mark.parametrize("fault", [None, "foreign_stage", "nonpositive", "missing"])
def test_actual_prometheus_drop_visibility_is_separate_and_strict(
    tmp_path: Path, fault: str | None
) -> None:
    from types import SimpleNamespace

    scenario = pressure_scenario(tmp_path)
    labels = {
        "job": "vector",
        "component_id": "storage",
        "buffer_id": "storage",
        "buffer_type": "disk",
        "stage": "1",
        "intentional": "true",
    }
    if fault == "foreign_stage":
        labels["stage"] = "0"
    data = {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [{"metric": labels, "value": [10, "0" if fault == "nonpositive" else "5"]}],
        },
    }
    calls = 0

    async def query(service: str, path: str, *, params: dict[str, str]) -> str:
        nonlocal calls
        calls += 1
        assert service == "prometheus" and path == "/api/v1/query"
        assert 'stage="1"' in params["query"] and "timestamp(" in params["query"]
        if fault == "missing":
            raise TimeoutError
        if calls == 1:
            return json.dumps({"status": "success", "data": {"resultType": "vector", "result": []}})
        return json.dumps(data)

    scenario.telemetry = SimpleNamespace(query=query)  # type: ignore[assignment]
    if fault:
        with pytest.raises((RuntimeError, TimeoutError)):
            asyncio.run(scenario.wait_drop_metric(None, 1))
        assert scenario.observations["prometheus_tail_discarded"] is None
    else:
        asyncio.run(scenario.wait_drop_metric(None, 1))
        assert calls == 2
        assert scenario.observations["prometheus_tail_discarded"]["value"] == 5


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1])
def test_nonfinite_native_buffer_metrics_are_fatal(tmp_path: Path, bad: float) -> None:
    scenario = pressure_scenario(tmp_path)
    with pytest.raises(RuntimeError, match="invalid native"):
        asyncio.run(scenario.buffer_pressure(metrics(bad, 1), None))


def test_new_counter_after_absent_baseline_is_not_a_measured_delta(tmp_path: Path) -> None:
    scenario = pressure_scenario(tmp_path)
    asyncio.run(scenario.buffer_pressure(metrics(134217288, 19), None))
    observation = scenario.observations["vector_pressure"]
    assert observation["baseline_discarded"] is None
    assert observation["latest"]["memory_tail_intentional_discarded"] == 19
    assert observation["latest"]["discarded_delta"] is None
    assert observation["peak_discarded_delta"] is None


def test_selected_chain_metric_labels_select_real_tail_counter(tmp_path: Path) -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures/vector-chain-native.json").read_text())
    observed: dict[str, list[dict[str, Any]]] = {}
    for sample in fixture["samples"]:
        observed.setdefault(sample["name"], []).append(sample)
    scenario = pressure_scenario(tmp_path)
    size, drops = asyncio.run(scenario.buffer_pressure(observed, None))
    assert size == 134217416
    assert drops == 68930
    assert scenario.observations["vector_pressure"]["drop_metric_labels"]["buffer_type"] == "disk"
