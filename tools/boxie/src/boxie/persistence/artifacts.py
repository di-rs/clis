"""Bounded generic ZIP staging with durable immutable IDs and reference retention."""

import asyncio
import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from collections.abc import AsyncIterable
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from boxie.domain.errors import BoxieError
from boxie.domain.models import ArtifactPutResult, ArtifactView, Principal
from boxie.persistence.artifact_files import extract_archive
from boxie.persistence.atomic import atomic_write, sync_directory


@dataclass(frozen=True)
class ArtifactLimits:
    compressed_bytes: int = 512 * 1024 * 1024
    expanded_bytes: int = 512 * 1024 * 1024
    member_bytes: int = 64 * 1024 * 1024
    files: int = 1024

    def __post_init__(self) -> None:
        if not (
            0 < self.compressed_bytes <= 512 * 1024 * 1024
            and 0 < self.expanded_bytes <= 512 * 1024 * 1024
            and 0 < self.member_bytes <= 64 * 1024 * 1024
            and 0 < self.files <= 1024
        ):
            raise ValueError("artifact limits must be positive and within service maxima")


@dataclass
class Metadata:
    consumer_id: str
    sha256: str
    retired: bool = False
    references: set[str] = field(default_factory=set)


class ArtifactStore:
    def __init__(self, root: Path, limits: ArtifactLimits | None = None) -> None:
        self.root, self.limits = root, limits or ArtifactLimits()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = asyncio.Lock()
        self._metadata: dict[UUID, Metadata] = {}
        # Staging directories never carry runnable metadata and cannot be mounted.
        for path in root.glob(".upload-*"):
            shutil.rmtree(path)
        for path in root.glob("*/metadata.json"):
            data = json.loads(path.read_text())
            self._metadata[UUID(path.parent.name)] = Metadata(
                data["consumer_id"], data["sha256"], data["retired"], set(data["references"])
            )
        for identifier, meta in self._metadata.items():
            if meta.retired and not meta.references:
                self._collect(identifier)

    def _save(self, identifier: UUID, meta: Metadata) -> None:
        atomic_write(
            self.root / str(identifier) / "metadata.json",
            json.dumps(
                {
                    "consumer_id": meta.consumer_id,
                    "sha256": meta.sha256,
                    "retired": meta.retired,
                    "references": sorted(meta.references),
                },
                sort_keys=True,
            ).encode(),
        )

    def _owned(self, principal: Principal, identifier: UUID) -> Metadata:
        meta = self._metadata.get(identifier)
        if meta is None or meta.consumer_id != principal.consumer_id:
            raise BoxieError(404, "artifact not found")
        return meta

    def exists(self, identifier: UUID) -> bool:
        return (self.root / str(identifier) / "files").is_dir()

    def path(self, principal: Principal, identifier: UUID) -> Path:
        self._owned(principal, identifier)
        path = self.root / str(identifier) / "files"
        if not path.is_dir():
            raise BoxieError(409, "artifact is retired")
        return path.resolve()

    async def put(
        self, principal: Principal, artifact_id: UUID, sha256: str, chunks: AsyncIterable[bytes]
    ) -> ArtifactPutResult:
        if not re.fullmatch(r"[a-f0-9]{64}", sha256):
            raise BoxieError(422, "invalid SHA-256 digest")
        async with self._lock:
            old = self._metadata.get(artifact_id)
            if old is not None:
                old = self._owned(principal, artifact_id)
                if old.sha256 != sha256 or old.retired:
                    raise BoxieError(409, "artifact ID is immutable or retired")
            staged = Path(tempfile.mkdtemp(prefix=".upload-", dir=self.root))
            try:
                digest, compressed = hashlib.sha256(), 0
                with (staged / "archive.zip").open("wb") as output:
                    async for chunk in chunks:
                        compressed += len(chunk)
                        if compressed > self.limits.compressed_bytes:
                            raise BoxieError(413, "compressed artifact is too large")
                        digest.update(chunk)
                        output.write(chunk)
                if digest.hexdigest() != sha256:
                    raise BoxieError(422, "artifact digest mismatch")
                if old is not None:
                    return ArtifactPutResult(ArtifactView(artifact_id, sha256, old.retired), False)
                await extract_archive(staged, self.limits)
                (staged / "archive.zip").unlink()
                meta = Metadata(principal.consumer_id, sha256)
                atomic_write(
                    staged / "metadata.json",
                    json.dumps(
                        {
                            "consumer_id": meta.consumer_id,
                            "sha256": sha256,
                            "retired": False,
                            "references": [],
                        }
                    ).encode(),
                )
                staged.replace(self.root / str(artifact_id))
                sync_directory(self.root)
                self._metadata[artifact_id] = meta
                return ArtifactPutResult(ArtifactView(artifact_id, sha256, False), True)
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
            finally:
                if staged.exists():
                    shutil.rmtree(staged)

    def retain(self, principal: Principal, identifier: UUID, reference: str) -> None:
        meta = self._owned(principal, identifier)
        if reference in meta.references:
            return
        if meta.retired:
            raise BoxieError(409, "artifact is retired")
        self.path(principal, identifier)
        meta.references.add(reference)
        self._save(identifier, meta)

    def release(self, principal: Principal, identifier: UUID, reference: str) -> None:
        meta = self._owned(principal, identifier)
        meta.references.discard(reference)
        self._save(identifier, meta)
        self._collect(identifier)

    async def retire(self, principal: Principal, artifact_id: UUID) -> None:
        async with self._lock:
            meta = self._owned(principal, artifact_id)
            meta.retired = True
            self._save(artifact_id, meta)
            self._collect(artifact_id)

    def _collect(self, identifier: UUID) -> None:
        meta = self._metadata[identifier]
        path = self.root / str(identifier) / "files"
        if meta.retired and not meta.references and path.exists():
            shutil.rmtree(path)
            sync_directory(path.parent)
