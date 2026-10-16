"""Journal retention measures allocated space and distinct accepted identities."""

import asyncio
import json
import os
import re
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from observability_infra import smoke_scenarios
from observability_infra.smoke_scenarios import Scenario


def test_journal_usage_counts_allocated_blocks_not_sparse_length(tmp_path: Path) -> None:
    journal = tmp_path / "system.journal"
    with journal.open("wb") as stream:
        stream.write(b"x" * 4096)
        stream.truncate(120 * 1024**2)
    archived = tmp_path / "archived.journal~"
    archived.write_bytes(b"y" * 8192)
    (tmp_path / "ignored.journal.backup").write_bytes(b"z" * 4096)
    usage = smoke_scenarios.journal_usage(tmp_path)
    assert usage == {
        "files": 2,
        "apparent_bytes": journal.stat().st_size + archived.stat().st_size,
        "allocated_bytes": 512 * (journal.stat().st_blocks + archived.stat().st_blocks),
    }
    assert usage["apparent_bytes"] > 100 * 1024**2
    assert 0 < usage["allocated_bytes"] < 32 * 1024**2


@pytest.mark.parametrize("kind", ["symlink", "directory", "excess"])
def test_journal_usage_rejects_unexpected_sources(tmp_path: Path, kind: str) -> None:
    if kind == "symlink":
        target = tmp_path / "target"
        target.write_bytes(b"private")
        (tmp_path / "system.journal").symlink_to(target)
    elif kind == "directory":
        (tmp_path / "system.journal").mkdir()
    else:
        for index in range(129):
            (tmp_path / f"{index}.journal").touch()
    with pytest.raises(RuntimeError):
        smoke_scenarios.journal_usage(tmp_path)


@pytest.mark.parametrize("error", [FileNotFoundError, PermissionError])
def test_journal_usage_tolerates_only_concurrent_disappearance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: type[OSError]
) -> None:
    def unavailable(*, follow_symlinks: bool) -> None:
        assert follow_symlinks is False
        raise error()

    entries = [SimpleNamespace(name="system.journal", stat=unavailable)]
    monkeypatch.setattr(os, "scandir", lambda _: nullcontext(iter(entries)))
    if error is FileNotFoundError:
        assert smoke_scenarios.journal_usage(tmp_path) == {
            "files": 0,
            "apparent_bytes": 0,
            "allocated_bytes": 0,
        }
    else:
        with pytest.raises(PermissionError):
            smoke_scenarios.journal_usage(tmp_path)


def retention_case(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, allocations: list[int]
) -> tuple[Scenario, list[str], list[str]]:
    scenario = object.__new__(Scenario)
    scenario.name = "journal-retention"
    scenario.root = tmp_path
    scenario.flood_instance = str(UUID(int=123))
    scenario.flood_id = None
    scenario.observations = {}
    batches: list[str] = []
    queries: list[str] = []

    async def metrics(_: str) -> dict[str, object]:
        return {}

    async def flood(_: int) -> None:
        batches.append(scenario.flood_instance)
        scenario.flood_id = "a" * 64

    async def accepted() -> set[str]:
        return {str(UUID(int=index)) for index in range(1, 21)}

    async def progress(_: str) -> None:
        pass

    async def command(*args: str) -> str:
        patterns = [arg.removeprefix("--grep=") for arg in args if arg.startswith("--grep=")]
        if patterns:
            queries.append(patterns[0])
            original = json.dumps({"instance_id": batches[0], "operation_id": str(UUID(int=1))})
            other = json.dumps({"instance_id": batches[1], "operation_id": str(UUID(int=1))})
            assert re.search(patterns[0], original)
            assert not re.search(patterns[0], other)
            return original if len(queries) == 1 else ""
        return ""

    def usage(_: Path) -> dict[str, int]:
        allocated = allocations.pop(0) if len(allocations) > 1 else allocations[0]
        return {"files": 8, "apparent_bytes": 120 * 1024**2, "allocated_bytes": allocated}

    async def sleep(_: float) -> None:
        pass

    scenario.telemetry = SimpleNamespace(metrics=metrics)  # type: ignore[assignment]
    monkeypatch.setattr(scenario, "flood", flood)
    monkeypatch.setattr(scenario, "accepted", accepted)
    monkeypatch.setattr(scenario, "progress", progress)
    monkeypatch.setattr(scenario, "command", command)
    monkeypatch.setattr(smoke_scenarios, "journal_usage", usage)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    return scenario, batches, queries


def test_retention_requires_original_disappearance_and_allocated_floor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scenario, batches, queries = retention_case(
        monkeypatch, tmp_path, [40 * 1024**2, 16 * 1024**2, 40 * 1024**2]
    )
    asyncio.run(scenario.pressure("sandbox"))
    assert len(batches) == 2 and batches[0] != batches[1]
    assert len(queries) == 3
    assert scenario.observations["retention_baseline_instance"] == batches[0]
    assert scenario.observations["retention_baseline_operation"] == str(UUID(int=1))
    assert scenario.observations["oldest_event_removed"] is True
    assert scenario.observations["journal_usage"]["allocated_bytes"] == 40 * 1024**2
    assert scenario.observations["journal_usage"]["apparent_bytes"] == 120 * 1024**2


def test_retention_preserves_ceiling_and_evidence_before_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scenario, _, queries = retention_case(monkeypatch, tmp_path, [101 * 1024**2])
    with pytest.raises(RuntimeError, match="size ceiling"):
        asyncio.run(scenario.pressure("sandbox"))
    assert not queries
    assert scenario.observations["journal_usage"]["allocated_bytes"] == 101 * 1024**2
    assert "oldest_event_removed" not in scenario.observations
