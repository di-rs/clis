"""Orchestrator-only concrete artifact bytes; PostgreSQL is the sole metadata authority."""

import asyncio
import hashlib
import os
import re
import shutil
import tempfile
import time
import zipfile
from collections.abc import AsyncIterable, AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import func
from sqlmodel import col, select

from boxie.domain.errors import BoxieError
from boxie.domain.models import ArtifactPutResult, ArtifactView, Principal
from boxie.persistence.artifact_files import extract_archive
from boxie.persistence.artifacts import ArtifactLimits
from boxie.persistence.atomic import sync_directory
from boxie.persistence.database import SessionFactory
from boxie.persistence.models import Artifact, SandboxArtifactRef
from boxie.persistence.postgres import keyed_lock

OPERATION_SECONDS = 20.0


class PostgresArtifactStore:
    def __init__(
        self, sessions: SessionFactory, root: Path, limits: ArtifactLimits | None = None
    ) -> None:
        self.sessions, self.root, self.limits = sessions, root, limits or ArtifactLimits()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        # One orchestrator owns this directory. Serializes uploads with orphan collection.
        self._files_lock = asyncio.Lock()
        self._scan: Iterator[os.DirEntry[str]] | None = None

    @asynccontextmanager
    async def _operation(self) -> AsyncIterator[None]:
        # One total deadline includes waiting for publication/collection ownership.
        # Uncertain commit bytes stay published for subsequent SQL reconciliation.
        try:
            async with asyncio.timeout(OPERATION_SECONDS), self._files_lock:
                yield
        except TimeoutError:
            raise BoxieError(503, "artifact storage unavailable") from None

    async def put(
        self, principal: Principal, artifact_id: UUID, sha256: str, chunks: AsyncIterable[bytes]
    ) -> ArtifactPutResult:
        if not re.fullmatch(r"[a-f0-9]{64}", sha256):
            raise BoxieError(422, "invalid SHA-256 digest")
        async with self._operation():
            staged = Path(tempfile.mkdtemp(prefix=".upload-", dir=self.root))
            try:
                digest, size = hashlib.sha256(), 0
                with (staged / "archive.zip").open("wb") as output:
                    async for chunk in chunks:
                        size += len(chunk)
                        if size > self.limits.compressed_bytes:
                            raise BoxieError(413, "compressed artifact is too large")
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if digest.hexdigest() != sha256:
                    raise BoxieError(422, "artifact digest mismatch")
                # Validate retries too; metadata retries cannot bypass archive bounds.
                await extract_archive(staged, self.limits)
                (staged / "archive.zip").unlink()
                for directory, _, _ in os.walk(staged, topdown=False):
                    sync_directory(Path(directory))
                async with self.sessions() as session, session.begin():
                    await keyed_lock(session, "artifact:" + str(artifact_id))
                    old = (
                        await session.exec(
                            select(Artifact).where(Artifact.id == artifact_id).with_for_update()
                        )
                    ).one_or_none()
                    if old is not None:
                        if old.consumer_id != principal.consumer_id:
                            raise BoxieError(404, "artifact not found")
                        if old.digest != sha256 or old.retired:
                            raise BoxieError(409, "artifact ID is immutable or retired")
                        return ArtifactPutResult(ArtifactView(artifact_id, sha256, False), False)
                    storage_id = uuid4()
                    # Complete durable bytes precede metadata visibility. A lost commit
                    # response is ambiguous: leave bytes for DB-based reconciliation.
                    staged.rename(self.root / str(storage_id))
                    sync_directory(self.root)
                    session.add(
                        Artifact(
                            id=artifact_id,
                            consumer_id=principal.consumer_id,
                            digest=sha256,
                            storage_id=storage_id,
                        )
                    )
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

    async def path(self, principal: Principal, artifact_id: UUID) -> Path:
        async with self.sessions() as session:
            row = await session.get(Artifact, artifact_id)
            if row is None or row.consumer_id != principal.consumer_id:
                raise BoxieError(404, "artifact not found")
            path = self.root / str(row.storage_id) / "files"
            if not path.is_dir():
                raise BoxieError(409, "artifact bytes unavailable")
            return path.resolve()

    async def retire(self, principal: Principal, artifact_id: UUID) -> None:
        async with self.sessions() as session, session.begin():
            row = (
                await session.exec(
                    select(Artifact).where(Artifact.id == artifact_id).with_for_update()
                )
            ).one_or_none()
            if row is None or row.consumer_id != principal.consumer_id:
                raise BoxieError(404, "artifact not found")
            row.retired = True
            row.collection_pending = row.collected_at is None
        # Physical deletion is separately retryable; retirement is durable even if
        # this process exits now. The orchestrator's collector periodically calls collect.
        await self.collect()

    async def collect(self, limit: int = 100) -> int:
        if not 1 <= limit <= 1000:
            raise ValueError("collection limit must be 1..1000")
        collected = 0
        async with self._operation(), self.sessions() as session, session.begin():
            rows = (
                await session.exec(
                    select(Artifact)
                    .where(
                        col(Artifact.retired).is_(True),
                        col(Artifact.collection_pending).is_(True),
                        ~select(SandboxArtifactRef)
                        .where(SandboxArtifactRef.artifact_id == Artifact.id)
                        .exists(),
                    )
                    .order_by(col(Artifact.created_at))
                    .with_for_update(skip_locked=True)
                    .limit(limit)
                )
            ).all()
            for row in rows:
                path = self.root / str(row.storage_id)
                try:
                    if path.exists():
                        shutil.rmtree(path)
                    sync_directory(self.root)
                except OSError:
                    continue
                row.collection_pending = False
                row.collected_at = cast(
                    datetime, await session.scalar(select(func.clock_timestamp()))
                )
                collected += 1
        return collected

    async def collect_orphans(self, *, minimum_age_seconds: int = 3600, limit: int = 100) -> int:
        """Bounded scan with a persistent in-process cursor; run on the sole owning orchestrator.

        Published directories without committed SQL metadata and abandoned uploads
        are unreachable. Serialize against publication, keep a grace period, and
        query PostgreSQL before removing anything; DB outage deletes nothing.
        """
        if minimum_age_seconds < 60 or not 1 <= limit <= 1000:
            raise ValueError("orphan collection requires bounded limit and >=60s grace")
        count = 0
        async with self._operation(), self.sessions() as session:
            # scandir is lazy; a bounded pass avoids materializing a large directory.
            if self._scan is None:
                self._scan = os.scandir(self.root)
            for _ in range(limit):
                entry = next(self._scan, None)
                if entry is None:
                    self._scan = None
                    break
                path = Path(entry.path)
                if (
                    not entry.is_dir(follow_symlinks=False)
                    or time.time() - entry.stat().st_mtime < minimum_age_seconds
                ):
                    continue
                if not path.name.startswith(".upload-"):
                    try:
                        identifier = UUID(path.name)
                    except ValueError:
                        continue
                    exists = (
                        await session.exec(
                            select(Artifact.id).where(Artifact.storage_id == identifier)
                        )
                    ).first()
                    if exists:
                        continue
                shutil.rmtree(path)
                count += 1
            if count:
                sync_directory(self.root)
        return count
