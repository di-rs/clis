"""Long pressure loops retain bounded samples and transient observed extrema."""

import copy
import json
from typing import Any

import pytest
from observability_infra import smoke_samples


def sample(current: int = 100, oom: int = 0, free: int = 900) -> dict[str, Any]:
    memory = {
        "memory.current": str(current),
        "memory.max": "1024",
        "memory.events": f"low 0\nhigh 0\nmax 0\noom {oom}\noom_kill 0\noom_group_kill 0",
    }
    return {
        "at": 1.0,
        "containers": [
            {
                "id": "abc",
                "name": "owned",
                "driver": {"Type": "journald", "Config": {}},
                "oom_killed": bool(oom),
                "memory": dict(memory, **{"pids.current": "3"}),
            }
        ],
        "systemd": {"owned.service": memory},
        "filesystems": {"/owned/journal": {"bytes": 1000, "free": free}},
        "driver": {"address_space_limit": 18446744073709551615, "max_rss_kib": current},
    }


def progress(seconds: float = 0.01) -> dict[str, Any]:
    return {
        "http_seconds": seconds,
        "roundtrip_seconds": seconds * 2,
        "terminal_marker_observed": True,
    }


def test_long_loop_keeps_transient_peaks_without_unbounded_history() -> None:
    observations: dict[str, Any] = {}
    for index in range(500):
        peak = index == 250
        smoke_samples.record_progress(
            observations,
            progress(0.5 if peak else 0.01),
            sample(950 if peak else 100, int(peak), 10 if peak else 900),
        )
    assert len(json.dumps(observations)) < 10000
    assert observations["progress"]["count"] == 500
    assert observations["progress"]["http_seconds_max"] == 0.5
    assert observations["progress"]["roundtrip_seconds_max"] == 1.0
    resources = observations["resources"]
    assert resources["first"]["containers"][0]["memory"]["memory.current"] == "100"
    assert resources["latest"]["containers"][0]["memory"]["memory.current"] == "100"
    peaks = resources["sampled_peaks"]
    assert peaks["containers"]["abc"]["memory_current_max"] == 950
    assert peaks["containers"]["abc"]["oom_killed_observed"] is True
    assert peaks["containers"]["abc"]["memory_events_max"]["oom"] == 1
    assert peaks["systemd"]["owned.service"]["memory_current_max"] == 950
    assert peaks["filesystems"]["/owned/journal"]["free_min"] == 10
    assert peaks["driver_max_rss_kib"] == 950
    assert peaks["containers"]["abc"]["memory_max"] == "1024"


@pytest.mark.parametrize(
    "fault", ["missing", "malformed", "oversized", "changed_cap", "bad_progress"]
)
def test_invalid_sample_preserves_prior_evidence(fault: str) -> None:
    observations: dict[str, Any] = {}
    smoke_samples.record_progress(observations, progress(), sample())
    before = copy.deepcopy(observations)
    broken = sample()
    probe = progress()
    if fault == "missing":
        del broken["containers"][0]["memory"]["memory.current"]
    elif fault == "malformed":
        broken["systemd"]["owned.service"]["memory.events"] = "oom PRIVATE"
    elif fault == "oversized":
        broken["containers"][0]["name"] = "x" * 32768
    elif fault == "changed_cap":
        broken["containers"][0]["memory"]["memory.max"] = "2048"
    else:
        probe["roundtrip_seconds"] = float("nan")
    with pytest.raises((RuntimeError, ValueError, KeyError)):
        smoke_samples.record_progress(observations, probe, broken)
    assert observations == before


def test_peak_identity_growth_is_bounded_without_losing_prior_evidence() -> None:
    observations: dict[str, Any] = {}
    for index in range(1000):
        current = sample()
        current["containers"][0]["id"] = str(index)
        before = copy.deepcopy(observations)
        try:
            smoke_samples.record_progress(observations, progress(), current)
        except RuntimeError:
            assert observations == before
            assert len(json.dumps(observations)) < 100000
            break
    else:
        pytest.fail("unbounded resource identity growth")
