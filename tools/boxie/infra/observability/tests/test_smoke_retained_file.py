"""Retention proves removal of the exact file containing an accepted event."""

import asyncio
import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from observability_infra import smoke_retention

INSTANCE = str(UUID(int=123))
OPERATION = str(UUID(int=1))


def event(instance: str = INSTANCE) -> str:
    return json.dumps({"instance_id": instance, "operation_id": OPERATION})


@pytest.mark.parametrize("cancel", [False, True])
def test_retained_file_requires_unlink_not_rename_and_always_closes(
    tmp_path: Path, cancel: bool
) -> None:
    path = tmp_path / "system@baseline.journal"
    path.write_bytes(b"owned archive")
    observed_fd = -1

    async def query(*args: str) -> str:
        assert "--file=" + str(path) in args
        return event()

    async def run() -> None:
        nonlocal observed_fd
        async with smoke_retention.retained_archive(tmp_path, query, INSTANCE, OPERATION) as (
            fd,
            identity,
        ):
            observed_fd = fd
            assert not os.get_inheritable(fd)
            assert identity["baseline_event_observed"] is True
            assert identity["fd_inheritable"] is False
            assert identity["initial_link_count"] == 1
            assert identity["inode"] == path.stat().st_ino
            renamed = path.with_name("system@renamed.journal")
            path.rename(renamed)
            assert smoke_retention.removal_observation(tmp_path, fd, identity) == {
                "link_count": 1,
                "inode_present": True,
                "removed": False,
            }
            renamed.unlink()
            path.write_bytes(b"replacement")
            assert path.stat().st_ino != identity["inode"]
            assert smoke_retention.removal_observation(tmp_path, fd, identity) == {
                "link_count": 0,
                "inode_present": False,
                "removed": True,
            }
            if cancel:
                raise asyncio.CancelledError

    if cancel:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(run())
    else:
        asyncio.run(run())
    with pytest.raises(OSError):
        os.fstat(observed_fd)


@pytest.mark.parametrize("failure", ["replace", "query", "wrong_instance", "symlink", "fifo"])
def test_baseline_lookup_failure_never_proves_retention_and_closes_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    path = tmp_path / "system@baseline.journal"
    path.write_bytes(b"owned archive")
    if failure == "symlink":
        path.rename(tmp_path / "target")
        path.symlink_to(tmp_path / "target")
    if failure == "fifo":
        path.unlink()
        os.mkfifo(path)
    opened: list[int] = []
    real_open = os.open

    def tracked_open(*args: Any, **kwargs: Any) -> int:
        if failure == "fifo":
            assert args[1] & os.O_NONBLOCK  # Fail before a regressed FIFO open can block.
        fd = real_open(*args, **kwargs)
        opened.append(fd)
        return fd

    monkeypatch.setattr(os, "open", tracked_open)

    async def query(*args: str) -> str:
        if failure == "replace":
            path.unlink()
            path.write_bytes(b"replacement")
        if failure == "query":
            raise RuntimeError("journal query error")
        return event(str(UUID(int=124)) if failure == "wrong_instance" else INSTANCE)

    async def run() -> None:
        async with smoke_retention.retained_archive(tmp_path, query, INSTANCE, OPERATION):
            pytest.fail("invalid baseline accepted")

    with pytest.raises((RuntimeError, OSError)):
        asyncio.run(run())
    for fd in opened:
        with pytest.raises(OSError):
            os.fstat(fd)
