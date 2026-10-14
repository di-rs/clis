"""Artifact and sandbox lifecycle, independent of the HTTP transport."""

import asyncio
import logging
import shutil
import zipfile
import zlib
from collections.abc import AsyncIterable
from pathlib import Path

from anyio import CancelScope

from playground.artifacts import stage_archive
from playground.config import Settings
from playground.models import ArtifactIdentity, Environment, EnvironmentMetadata
from playground.sessions import SessionManager
from playground.storage import EnvironmentStore

logger = logging.getLogger(__name__)


class UploadBusyError(Exception):
    """Another artifact upload is holding the deployment lock."""


class UploadTooLargeError(Exception):
    """An artifact exceeded the configured size limit."""


class PlaygroundService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = EnvironmentStore(settings)
        self.sessions = SessionManager(settings)
        self.uploads = asyncio.Semaphore(1)
        self.updating: set[str] = set()

    async def cleanup_inactive(self) -> None:
        await self.sessions.retry_cleanup()
        identifiers = self.store.inactive_identifiers()
        identifiers.update(
            session.environment.id
            for session in self.sessions.active
            if self.store.get(session.environment.id) is None
        )
        await self.sessions.close_environments(identifiers)
        self.store.remove(identifiers)

    async def cleanup_periodically(self) -> None:
        while True:
            await asyncio.sleep(self.settings.cleanup_interval_seconds)
            try:
                async with self.uploads:
                    await self.cleanup_inactive()
            except OSError, RuntimeError, TimeoutError:
                logger.exception("Failed to expire playground environments")

    async def deploy(self, chunks: AsyncIterable[bytes], sha: str, pr: int) -> Environment:
        artifact = ArtifactIdentity(sha=sha, pr=pr)
        try:
            await asyncio.wait_for(self.uploads.acquire(), timeout=1)
        except TimeoutError as error:
            raise UploadBusyError from error
        temporary: Path | None = None
        try:
            async with asyncio.timeout(60):
                await self.cleanup_inactive()
                temporary = self.store.temporary_directory()
                size = 0
                archive = temporary / "bundle.zip"
                with archive.open("wb") as binary:
                    async for chunk in chunks:
                        size += len(chunk)
                        if size > self.settings.max_archive_bytes:
                            raise UploadTooLargeError
                        binary.write(chunk)
                staged = temporary / "binaries"
                try:
                    staging = asyncio.create_task(
                        asyncio.to_thread(
                            stage_archive, archive, staged, self.settings.max_binary_bytes
                        )
                    )
                    try:
                        clis = await asyncio.shield(staging)
                    except asyncio.CancelledError:
                        # Threads cannot be cancelled. Keep their files and upload slot
                        # until they finish, even if cancellation is requested again.
                        finished = asyncio.gather(staging, return_exceptions=True)
                        with CancelScope(shield=True):
                            while not finished.done():
                                try:
                                    await asyncio.shield(finished)
                                except asyncio.CancelledError:
                                    pass
                        raise
                except OverflowError as error:
                    raise UploadTooLargeError from error
                except (zipfile.BadZipFile, zlib.error, RuntimeError) as error:
                    raise ValueError("invalid binary bundle") from error
                # Revisions may expire while the upload body is streaming.
                await self.cleanup_inactive()
                identifier = self.store.identifier(artifact.pr)
                existing = self.store.get(identifier)
                self.updating.add(identifier)
                try:
                    if existing is not None and existing.sha != artifact.sha:
                        await self.sessions.close_environments({identifier})
                    return self.store.publish(staged, clis, artifact.sha, artifact.pr)
                finally:
                    self.updating.discard(identifier)
        finally:
            if temporary is not None:
                shutil.rmtree(temporary, ignore_errors=True)
            self.uploads.release()

    async def close_pull_request(self, pr: int) -> int:
        # Publish must see the tombstone even while we wait for its upload lock.
        self.store.close_pull_request(pr)
        async with self.uploads:
            identifiers = self.store.for_pull_request(pr)
            identifiers.update(
                session.environment.id
                for session in self.sessions.active
                if session.environment.pr == pr
            )
            await self.sessions.close_environments(identifiers)
            return self.store.remove(identifiers)

    async def reopen_pull_request(self, pr: int) -> None:
        async with self.uploads:
            self.store.reopen_pull_request(pr)

    async def metadata(self, identifier: str) -> EnvironmentMetadata | None:
        environment = self.store.get(identifier)
        return environment.metadata() if environment is not None else None
