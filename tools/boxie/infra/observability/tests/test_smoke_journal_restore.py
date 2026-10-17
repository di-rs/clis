"""Owned namespace recovery is explicit and requires fresh end-to-end delivery."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from observability_infra import smoke_watchdog
from observability_infra.smoke_runtime import Commands, Deadline
from observability_infra.smoke_scenarios import Scenario


@pytest.mark.parametrize("failure", [None, "identity", "filler", "restart", "after"])
def test_registered_restart_requires_exact_original_and_resulting_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    directory = tmp_path / "journal"
    directory.mkdir()
    filler = directory / "filler-journal-enospc"
    filler.write_bytes(b"owned")
    identity = {"unit": "owned.service", "pid": 123, "start": "456", "cgroup": "/owned.service"}
    data = {
        "fillers": [
            {"path": str(filler), "inode": filler.stat().st_ino + int(failure == "filler")}
        ],
        "journal_restart": identity,
    }
    manifest = tmp_path / "watch-journal-enospc.json"
    manifest.write_text(json.dumps(data))
    calls: list[list[str]] = []
    read_count = 0

    def current(*args: object) -> dict[str, object]:
        nonlocal read_count
        read_count += 1
        if read_count == 1:
            return dict(identity, start="foreign") if failure == "identity" else identity
        return dict(
            identity,
            pid=124,
            start="457",
            cgroup="/foreign" if failure == "after" else "/owned.service",
        )

    commands = Commands(Deadline(2, reserve=0.1))

    def run(args: list[str], **kwargs: object) -> str:
        calls.append(args)
        if failure == "restart":
            raise RuntimeError("restart failed")
        return ""

    monkeypatch.setattr(commands, "run", run)
    monkeypatch.setattr(smoke_watchdog, "journal_identity", current)
    result = smoke_watchdog.restore_once(manifest, commands)
    assert result["journal_restarted"] is (failure is None)
    assert smoke_watchdog.restoration_valid(data, result) is (failure is None)
    assert bool(calls) is (failure not in {"identity", "filler"})
    if failure is None:
        assert calls == [["systemctl", "restart", identity["unit"]]]
        # A missing watchdog result uses the same exact-identity fallback path.
        read_count = 0
        fallback = smoke_watchdog.restore_registered(manifest, commands, wait_seconds=0)
        assert fallback["restored"] is True
        assert fallback["fallback"]["journal_restarted"] is True


@pytest.mark.parametrize("failure", [None, "persistent", "collector"])
def test_restart_is_not_delivery_recovery_without_fresh_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    scenario = object.__new__(Scenario)
    scenario.root = tmp_path
    scenario.name = "journal-enospc"
    scenario.flood_id = None
    scenario.flood_instance = str(UUID(int=20))
    scenario.observations = {"flood_id": "old", "flood_name": "old-name"}
    seen: list[str] = []

    async def flood(limit: int, *, recovery: bool = False) -> None:
        assert limit == 1 and recovery
        seen.append(scenario.flood_instance)
        scenario.flood_id = "a" * 64
        scenario.observations.update(flood_id=scenario.flood_id, flood_name="new")

    async def accepted() -> set[str]:
        if failure == "persistent":
            raise TimeoutError
        scenario.observations["journal_acceptance_read"] = {
            "complete": False,
            "warnings": {"truncated_file": 1},
        }
        return {str(UUID(int=1))}

    async def logs(query: str) -> list[dict[str, str]]:
        assert seen[0] in query
        if failure == "collector":
            raise TimeoutError
        return [{"operation_id": str(UUID(int=1))}]

    monkeypatch.setattr(scenario, "flood", flood)
    monkeypatch.setattr(scenario, "accepted", accepted)
    scenario.telemetry = SimpleNamespace(wait_logs=logs)  # type: ignore[assignment]
    scenario.command = AsyncMock(return_value="")  # type: ignore[method-assign]
    if failure:
        with pytest.raises(TimeoutError):
            asyncio.run(scenario.journal_recovery_probe())
    else:
        asyncio.run(scenario.journal_recovery_probe())
    proof = scenario.observations["journal_delivery_recovery"]
    assert seen[0] != str(UUID(int=20))
    assert proof["persistent_observed"] is (failure != "persistent")
    assert proof["collector_observed"] is (failure is None)
    if failure != "persistent":
        assert proof["persistent_read"]["complete"] is False
    assert scenario.flood_instance == str(UUID(int=20))
    assert scenario.flood_id is None
    assert scenario.observations["flood_id"] == "old"
    assert isinstance(scenario.command, AsyncMock)
    scenario.command.assert_called_once_with("docker", "rm", "--force", "a" * 64)


@pytest.mark.parametrize("registered", [False, True])
def test_namespace_identity_requires_existing_matching_owned_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: bool
) -> None:
    from observability_infra import smoke_resources

    manifest = tmp_path / "ownership.json"
    unit = "systemd-journald@owned.service"
    if registered:
        manifest.write_text("{}")
    resource = SimpleNamespace(
        manifest=manifest,
        journal_unit=unit,
        state={"journal_unit": "foreign.service"},
        owned_cgroup=lambda _: "/system.slice/" + unit,
    )
    monkeypatch.setattr(smoke_resources, "require_ephemeral_runner", lambda: None)
    monkeypatch.setattr(smoke_resources, "Resources", lambda *args: resource)
    with pytest.raises(RuntimeError, match="foreign registered"):
        smoke_watchdog.journal_identity(tmp_path, Commands(Deadline(2, reserve=0.1)))
