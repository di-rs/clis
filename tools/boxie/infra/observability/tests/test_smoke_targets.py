"""Native success requires every configured private exporter, not only valid rules."""

from datetime import UTC, datetime, timedelta

import pytest
from observability_infra import smoke_telemetry


def target(job: str = "boxie-orchestrator", **changes: object) -> dict[str, object]:
    return {
        "labels": {"job": job},
        "scrapeUrl": f"http://{job}:9103/metrics",
        "health": "up",
        "lastScrape": datetime.now(UTC).isoformat(),
        **changes,
    }


def test_all_required_targets_must_be_fresh_and_reachable() -> None:
    assert callable(getattr(smoke_telemetry, "target_health", None))
    expected = {"boxie-orchestrator": "boxie-orchestrator:9103"}
    for entries in (
        [],
        [target(health="down")],
        [target(lastScrape=(datetime.now(UTC) - timedelta(seconds=46)).isoformat())],
    ):
        assert smoke_telemetry.target_health({"data": {"activeTargets": entries}}, expected) == {
            "boxie-orchestrator": False
        }
    assert smoke_telemetry.target_health({"data": {"activeTargets": [target()]}}, expected) == {
        "boxie-orchestrator": True
    }


def test_foreign_and_duplicate_target_identity_never_passes() -> None:
    assert callable(getattr(smoke_telemetry, "target_health", None))
    expected = {"boxie-orchestrator": "boxie-orchestrator:9103"}
    for entries in (
        [target(), target()],
        [target("playground")],
        [target(scrapeUrl="http://foreign:9103/metrics")],
    ):
        with pytest.raises(ValueError):
            smoke_telemetry.target_health({"data": {"activeTargets": entries}}, expected)
