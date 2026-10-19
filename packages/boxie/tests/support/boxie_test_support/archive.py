"""ZIPs are generic bytes; filesystem effects and ownership are the contract."""

import asyncio
import shutil
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

from boxie.domain.errors import BoxieError
from boxie.domain.models import Principal
from boxie.persistence.artifact_files import extract_archive
from boxie.persistence.artifacts import ArtifactLimits


class Archive:
    """Extraction harness only; SQL publication/ownership is covered in test_postgres."""

    def __init__(self, root: Path, limits: ArtifactLimits | None = None) -> None:
        self.root, self.limits = root, limits or ArtifactLimits()

    def path(self, principal: Principal, identifier: UUID) -> Path:
        return self.root / str(identifier) / "files"

    def exists(self, identifier: UUID) -> bool:
        return (self.root / str(identifier) / "files").exists()

    async def put(
        self, principal: Principal, identifier: UUID, digest: str, data: AsyncIterator[bytes]
    ) -> None:
        staged = self.root / str(identifier)
        staged.mkdir()
        try:
            with (staged / "archive.zip").open("wb") as output:
                async for chunk in data:
                    output.write(chunk)
            try:
                await extract_archive(staged, self.limits)
            except (
                zipfile.BadZipFile,
                NotImplementedError,
                RuntimeError,
                EOFError,
                FileExistsError,
                NotADirectoryError,
                IsADirectoryError,
                ValueError,
            ) as error:
                raise BoxieError(422, "invalid ZIP artifact") from error
        except BaseException:
            shutil.rmtree(staged)
            raise


async def chunks(data: bytes) -> AsyncIterator[bytes]:
    for offset in range(0, len(data), 7):
        yield data[offset : offset + 7]
        await asyncio.sleep(0)
