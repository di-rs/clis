"""Explicit in-memory metadata boundary for terminal-only tests, with real ZIP extraction.

No persistence, SQL accounting or publication durability is claimed by this helper.
"""

import hashlib
import shutil
from collections.abc import AsyncIterable
from pathlib import Path
from uuid import UUID

from boxie.domain.errors import BoxieError
from boxie.domain.models import ArtifactPutResult, ArtifactView, Principal
from boxie.persistence.artifact_files import extract_archive
from boxie.persistence.artifacts import ArtifactLimits


class Artifacts:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.items: dict[UUID, tuple[Principal, ArtifactView]] = {}

    def path(self, principal: Principal, identifier: UUID) -> Path:
        item = self.items.get(identifier)
        if item is None or item[0] != principal:
            raise BoxieError(404, "artifact not found")
        if item[1].retired:
            raise BoxieError(409, "artifact retired")
        return self.root / str(identifier) / "files"

    async def put(
        self, principal: Principal, artifact_id: UUID, sha256: str, chunks: AsyncIterable[bytes]
    ) -> ArtifactPutResult:
        data = bytearray()
        async for chunk in chunks:
            data.extend(chunk)
        if hashlib.sha256(data).hexdigest() != sha256:
            raise BoxieError(422, "artifact digest mismatch")
        previous = self.items.get(artifact_id)
        if previous:
            self.path(principal, artifact_id)
            if previous[1].sha256 != sha256:
                raise BoxieError(409, "artifact immutable")
            return ArtifactPutResult(previous[1], False)
        staged = self.root / str(artifact_id)
        staged.mkdir()
        (staged / "archive.zip").write_bytes(data)
        try:
            await extract_archive(staged, ArtifactLimits())
        except BaseException:
            shutil.rmtree(staged)
            raise
        view = ArtifactView(artifact_id, sha256, False)
        self.items[artifact_id] = (principal, view)
        return ArtifactPutResult(view, True)

    async def retire(self, principal: Principal, artifact_id: UUID) -> None:
        previous = self.items.get(artifact_id)
        if previous is None or previous[0] != principal:
            raise BoxieError(404, "artifact not found")
        self.items[artifact_id] = (principal, ArtifactView(artifact_id, previous[1].sha256, True))
