"""Only never-evaluated rule health may wait within the existing case deadline."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from observability_infra.smoke_runtime import Commands, Deadline
from observability_infra.smoke_scenarios import Scenario


def payload(
    health: str = "unknown", evaluation: str = "0001-01-01T00:00:00Z", error: str = ""
) -> str:
    return json.dumps(
        {
            "data": {
                "groups": [
                    {
                        "rules": [
                            {
                                "health": health,
                                "lastEvaluation": evaluation,
                                "lastError": error,
                                "query": "PRIVATE_QUERY",
                                "labels": {"secret": "PRIVATE_LABEL"},
                            }
                        ]
                    }
                ]
            }
        }
    )


def scenario_with(responses: list[str]) -> tuple[Scenario, list[str]]:
    seen: list[str] = []

    async def query(*args: object, **kwargs: object) -> str:
        seen.append("queried")
        return responses[min(len(seen) - 1, len(responses) - 1)]

    scenario = object.__new__(Scenario)
    scenario.commands = Commands(Deadline(0.5, reserve=0.1))
    scenario.observations = {}
    scenario.telemetry = SimpleNamespace(query=query)  # type: ignore[assignment]
    return scenario, seen


def test_initial_unknown_waits_for_real_healthy_evaluation() -> None:
    scenario, seen = scenario_with([payload(), payload("ok", "2026-10-16T12:00:00Z")])
    assert asyncio.run(scenario.wait_rules()) == 1
    assert len(seen) == 2
    assert scenario.observations["rules_initial"] != scenario.observations["rules_current"]
    assert "PRIVATE" not in json.dumps(scenario.observations)


@pytest.mark.parametrize(
    "response",
    [
        payload("err", error="PRIVATE_ERROR"),
        payload("unknown", "2026-10-16T12:00:00Z"),
        payload("unknown", "malformed"),
        payload("unrecognized"),
        payload("ok", "2026-10-16T12:00:00Z", "PRIVATE_ERROR"),
        json.dumps({"data": {"groups": []}}),
        json.dumps({"data": {"groups": [{"rules": []}]}}),
        json.dumps({"data": {"groups": [{"rules": [{}]}]}}),
        json.dumps({"data": {"groups": [{"rules": [{}] * 129}]}}),
    ],
)
def test_errors_and_noninitial_unknown_never_retry_or_disclose(response: str) -> None:
    scenario, seen = scenario_with([response, payload("ok", "2026-10-16T12:00:00Z")])
    with pytest.raises(RuntimeError):
        asyncio.run(scenario.wait_rules())
    assert seen == ["queried"]
    assert "rules_current" in scenario.observations
    assert "PRIVATE" not in json.dumps(scenario.observations)


def test_never_evaluated_rules_still_exhaust_existing_deadline() -> None:
    scenario, _ = scenario_with([payload()])
    with pytest.raises(TimeoutError):
        asyncio.run(scenario.wait_rules())
    assert scenario.observations["rules_current"]
