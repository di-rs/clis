"""Agent-only concrete artifact bytes; PostgreSQL is the sole metadata authority."""

import asyncio
import hashlib
import os
import re
import shutil
import tempfile
import time
import zipfile
from collections.abc import AsyncIterable, Iterator
from pathlib import Path
from uuid import UUID, uuid4

from boxie.domain.errors import BoxieError
from boxie.domain.models import ArtifactPutResult, ArtifactView, Principal
from boxie.persistence.artifact_files import extract_archive
from boxie.persistence.artifacts import ArtifactLimits
from boxie.persistence.atomic import sync_directory
from boxie.persistence.postgres import Pool, keyed_lock


class PostgresArtifactStore:
    def __init__(self, pool: Pool, root: Path, limits: ArtifactLimits | None = None) -> None:
        self.pool, self.root, self.limits = pool, root, limits or ArtifactLimits()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        # One agent owns this directory. Serializes uploads with orphan collection.
        self._files_lock = asyncio.Lock()
        self._scan: Iterator[os.DirEntry[str]] | None = None

    async def put(
        self, principal: Principal, artifact_id: UUID, sha256: str, chunks: AsyncIterable[bytes]
    ) -> ArtifactPutResult:
        if not re.fullmatch(r"[a-f0-9]{64}", sha256):
            raise BoxieError(422, "invalid SHA-256 digest")
        async with self._files_lock:
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
                async with self.pool.connection() as conn, conn.transaction():
                    await keyed_lock(conn, "artifact:" + str(artifact_id))
                    old = await (
                        await conn.execute(
                            "SELECT * FROM artifacts WHERE id=%s FOR UPDATE", (artifact_id,)
                        )
                    ).fetchone()
                    if old is not None:
                        if old["consumer_id"] != principal.consumer_id:
                            raise BoxieError(404, "artifact not found")
                        if old["digest"] != sha256 or old["retired"]:
                            raise BoxieError(409, "artifact ID is immutable or retired")
                        return ArtifactPutResult(ArtifactView(artifact_id, sha256, False), False)
                    storage_id = uuid4()
                    # Complete durable bytes precede metadata visibility. A lost commit
                    # response is ambiguous: leave bytes for DB-based reconciliation.
                    staged.rename(self.root / str(storage_id))
                    sync_directory(self.root)
                    await conn.execute(
                        "INSERT INTO artifacts (id,consumer_id,digest,storage_id) VALUES (%s,%s,%s,%s)",
                        (artifact_id, principal.consumer_id, sha256, storage_id),
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
        async with self.pool.connection() as conn:
            row = await (
                await conn.execute("SELECT * FROM artifacts WHERE id=%s", (artifact_id,))
            ).fetchone()
            if row is None or row["consumer_id"] != principal.consumer_id:
                raise BoxieError(404, "artifact not found")
            path = self.root / str(row["storage_id"]) / "files"
            if not path.is_dir():
                raise BoxieError(409, "artifact bytes unavailable")
            return path.resolve()

    async def retire(self, principal: Principal, artifact_id: UUID) -> None:
        async with self.pool.connection() as conn, conn.transaction():
            row = await (
                await conn.execute("SELECT * FROM artifacts WHERE id=%s FOR UPDATE", (artifact_id,))
            ).fetchone()
            if row is None or row["consumer_id"] != principal.consumer_id:
                raise BoxieError(404, "artifact not found")
            await conn.execute(
                "UPDATE artifacts SET retired=true,collection_pending=(collected_at IS NULL) WHERE id=%s",
                (artifact_id,),
            )
        # Physical deletion is separately retryable; retirement is durable even if
        # this process exits now. The agent's collector periodically calls collect.
        await self.collect()

    async def collect(self, limit: int = 100) -> int:
        if not 1 <= limit <= 1000:
            raise ValueError("collection limit must be 1..1000")
        collected = 0
        async with self._files_lock, self.pool.connection() as conn, conn.transaction():
            rows = await (
                await conn.execute(
                    """SELECT * FROM artifacts a WHERE retired
                AND collection_pending AND NOT EXISTS
                (SELECT 1 FROM sandbox_artifact_refs r WHERE r.artifact_id=a.id)
                ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT %s""",
                    (limit,),
                )
            ).fetchall()
            for row in rows:
                path = self.root / str(row["storage_id"])
                try:
                    if path.exists():
                        shutil.rmtree(path)
                    sync_directory(self.root)
                except OSError:
                    continue
                await conn.execute(
                    "UPDATE artifacts SET collection_pending=false,collected_at=clock_timestamp() WHERE id=%s",
                    (row["id"],),
                )
                collected += 1
        return collected

    async def collect_orphans(self, *, minimum_age_seconds: int = 3600, limit: int = 100) -> int:
        """Bounded scan with a persistent in-process cursor; run on the sole owning agent.

        Published directories without committed SQL metadata and abandoned uploads
        are unreachable. Serialize against publication, keep a grace period, and
        query PostgreSQL before removing anything; DB outage deletes nothing.
        """
        if minimum_age_seconds < 60 or not 1 <= limit <= 1000:
            raise ValueError("orphan collection requires bounded limit and >=60s grace")
        count = 0
        async with self._files_lock, self.pool.connection() as conn:
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
                    exists = await (
                        await conn.execute(
                            "SELECT 1 FROM artifacts WHERE storage_id=%s", (identifier,)
                        )
                    ).fetchone()
                    if exists:
                        continue
                shutil.rmtree(path)
                count += 1
            if count:
                sync_directory(self.root)
        return count
