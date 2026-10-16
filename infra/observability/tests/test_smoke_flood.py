"""Finite synthetic pressure pacing preserves upstream breathing room."""

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from observability_infra import smoke_flood
from observability_infra.smoke_scenarios import Scenario


@pytest.mark.parametrize("slow_batch", [False, True])
def test_paced_batches_are_finite_and_never_catch_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, slow_batch: bool
) -> None:
    clock = 0.0
    writes: list[tuple[float, dict[str, object]]] = []

    def sleep(seconds: float) -> None:
        nonlocal clock
        assert seconds > 0
        clock += seconds

    def write(fd: int, data: bytes) -> int:
        nonlocal clock
        assert fd == 1
        writes.append((clock, json.loads(data)))
        if slow_batch and len(writes) == 1000:
            clock += 2
        return len(data)

    monkeypatch.setattr(sys, "argv", ["flood.py", "instance", "2001", "--paced"])
    monkeypatch.setattr(smoke_flood, "Path", lambda _: tmp_path)
    monkeypatch.setattr(time, "monotonic", lambda: clock)
    monkeypatch.setattr(time, "sleep", sleep)
    monkeypatch.setattr(os, "write", write)
    smoke_flood.main()
    assert len(writes) == 2001
    assert writes[0][0] >= 0.1
    assert writes[1000][0] - writes[999][0] >= (2.1 if slow_batch else 0.1) - 1e-9
    assert writes[2000][0] - writes[1999][0] >= 0.1 - 1e-9
    assert {event["instance_id"] for _, event in writes} == {"instance"}
    assert json.loads((tmp_path / "emitted.json").read_text())["emitted"] == 2001


def test_pacing_does_not_allow_an_increased_event_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["flood.py", "instance", "1000001", "--paced"])
    with pytest.raises(ValueError, match="event bound"):
        smoke_flood.main()


def test_unpaced_journal_probe_keeps_its_original_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writes: list[dict[str, object]] = []
    monkeypatch.setattr(sys, "argv", ["flood.py", "pressure", "33", "probe"])
    monkeypatch.setattr(smoke_flood, "Path", lambda _: tmp_path)
    monkeypatch.setattr(time, "sleep", lambda _: pytest.fail("unpaced flood slept"))
    monkeypatch.setattr(os, "write", lambda _, data: writes.append(json.loads(data)))
    smoke_flood.main()
    assert [event["instance_id"] for event in writes] == ["probe"] * 32 + ["pressure"]
    assert json.loads((tmp_path / "emitted.json").read_text())["probe_emitted"] == 32


def test_interrupted_pacing_does_not_emit_another_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = 0.0
    waits = writes = 0

    def sleep(seconds: float) -> None:
        nonlocal clock, waits
        waits += 1
        if waits == 2:
            raise KeyboardInterrupt
        clock += seconds

    def write(fd: int, data: bytes) -> int:
        nonlocal writes
        writes += 1
        return len(data)

    monkeypatch.setattr(sys, "argv", ["flood.py", "pressure", "1000000", "--paced"])
    monkeypatch.setattr(smoke_flood, "Path", lambda _: tmp_path)
    monkeypatch.setattr(time, "monotonic", lambda: clock)
    monkeypatch.setattr(time, "sleep", sleep)
    monkeypatch.setattr(os, "write", write)
    with pytest.raises(KeyboardInterrupt):
        smoke_flood.main()
    assert writes == 1000


@pytest.mark.parametrize("name", ["vector-full", "vector-enospc", "collector-restart"])
def test_only_vector_full_requests_pacing(tmp_path: Path, name: str) -> None:
    scenario = object.__new__(Scenario)
    scenario.root = tmp_path
    scenario.name = name
    scenario.flood_instance = "instance"
    scenario.observations = {}
    scenario.command = AsyncMock(return_value="a" * 64)  # type: ignore[method-assign]
    asyncio.run(scenario.flood(1000000))
    assert isinstance(scenario.command, AsyncMock)
    assert ("--paced" in scenario.command.call_args.args) is (name == "vector-full")
    assert ("pressure_pacing" in scenario.observations) is (name == "vector-full")
