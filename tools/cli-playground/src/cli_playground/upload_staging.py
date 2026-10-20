"""Durable ZIP staging; structured state belongs to PostgreSQL."""

import asyncio
import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

from anyio import CancelScope


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class UploadStaging:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.uploads = root / "uploads"
        self.uploads.mkdir(parents=True, exist_ok=True, mode=0o700)

    def temporary_directory(self) -> Path:
        return Path(tempfile.mkdtemp(prefix=".upload-", dir=self.root))

    def archive(self, identifier: UUID) -> Path:
        return self.uploads / f"{identifier}.zip"

    def stage(self, identifier: UUID, source: Path) -> None:
        target = self.archive(identifier)
        temporary = target.with_suffix(".pending")
        with source.open("rb") as incoming, temporary.open("wb") as output:
            shutil.copyfileobj(incoming, output)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(target)
        sync_directory(self.uploads)

    def remove(self, identifier: UUID) -> None:
        self.archive(identifier).unlink(missing_ok=True)
        sync_directory(self.uploads)

    def recover(self, pending: set[UUID]) -> None:
        for directory in self.root.glob(".upload-*"):
            shutil.rmtree(directory)
        for path in self.uploads.iterdir():
            if path.suffix == ".pending" or (
                path.suffix == ".zip" and UUID(path.stem) not in pending
            ):
                path.unlink()
        sync_directory(self.uploads)


async def run_staging[T](operation: Callable[[], T]) -> T:
    task = asyncio.create_task(asyncio.to_thread(operation))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # A cancelled request must keep the source files alive until its worker exits.
        finished = asyncio.gather(task, return_exceptions=True)
        with CancelScope(shield=True):
            while not finished.done():
                try:
                    await asyncio.shield(finished)
                except asyncio.CancelledError:
                    pass
        raise
