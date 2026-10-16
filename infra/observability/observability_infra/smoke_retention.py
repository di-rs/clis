"""Observe exact accepted archive deletion without reading files being vacuumed."""

import os
import re
import stat
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from observability_infra.smoke_runtime import event_identities


def journal_paths(directory: Path) -> list[Path]:
    paths: list[Path] = []
    with os.scandir(directory) as entries:
        for index, entry in enumerate(entries):
            if index >= 128:
                raise RuntimeError("journal identity inventory exceeded bound")
            if entry.name.endswith((".journal", ".journal~")):
                paths.append(directory / entry.name)
    return sorted(paths)


def file_identity(info: os.stat_result) -> tuple[int, int]:
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError("unexpected journal identity source")
    return info.st_dev, info.st_ino


@asynccontextmanager
async def retained_archive(
    directory: Path,
    command: Callable[..., Awaitable[str]],
    instance: str,
    operation: str,
) -> AsyncIterator[tuple[int, dict[str, Any]]]:
    for path in journal_paths(directory):
        if "@" not in path.name or not path.name.endswith(".journal"):
            continue
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            before = os.fstat(fd)
            expected = file_identity(before)
            if before.st_nlink != 1 or os.get_inheritable(fd):
                raise RuntimeError("invalid retained journal descriptor")
            if file_identity(path.stat(follow_symlinks=False)) != expected:
                raise RuntimeError("baseline journal path identity changed")
            # Read only this archived file, before pressure starts. Never count
            # a journal read error as proof that an accepted event disappeared.
            text = await command(
                "journalctl",
                "--file=" + str(path),
                "--no-pager",
                "--output=cat",
                "--grep="
                + re.escape('"instance_id": "' + instance + '"')
                + ".*"
                + re.escape('"operation_id": "' + operation + '"'),
                "--lines=1",
            )
            after = os.fstat(fd)
            if (
                file_identity(after) != expected
                or after.st_nlink != 1
                or file_identity(path.stat(follow_symlinks=False)) != expected
            ):
                raise RuntimeError("baseline journal path identity changed")
            if event_identities(text, instance) != {operation}:
                continue
            yield (
                fd,
                {
                    "name": path.name,
                    "device": before.st_dev,
                    "inode": before.st_ino,
                    "initial_link_count": before.st_nlink,
                    "baseline_event_observed": True,
                    "fd_inheritable": os.get_inheritable(fd),
                },
            )
            return
        finally:
            os.close(fd)
    raise RuntimeError("accepted baseline event not found in an owned archive")


def removal_observation(directory: Path, fd: int, identity: dict[str, Any]) -> dict[str, Any]:
    retained = os.fstat(fd)
    expected = identity["device"], identity["inode"]
    if file_identity(retained) != expected:
        raise RuntimeError("retained journal descriptor identity changed")
    present = False
    for path in journal_paths(directory):
        try:
            info = path.stat(follow_symlinks=False)
        except FileNotFoundError:  # Another archive was concurrently vacuumed.
            continue
        if file_identity(info) == expected:
            present = True
    return {
        "link_count": retained.st_nlink,
        "inode_present": present,
        "removed": retained.st_nlink == 0 and not present,
    }
