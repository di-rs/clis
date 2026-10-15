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
from playground.models import (
    ArtifactIdentity,
    BuildIdentity,
    BuildUpdate,
    Environment,
    EnvironmentMetadata,
)
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

    async def cleanup_inactive(self) -> None:
        await self.sessions.retry_cleanup()
        identifiers = self.store.inactive_identifiers()
        identifiers.update(
            session.environment.id
            for session in self.sessions.active
            if self.store.record(session.environment.id) is None
        )
        await self.sessions.close_environments(identifiers)
        # A build update can refresh a record while container shutdown yields.
        # Recheck without awaiting so cleanup cannot erase the accepted update.
        self.store.remove(
            {identifier for identifier in identifiers if self.store.record(identifier) is None}
        )
        self.collect_artifacts()

    def collect_artifacts(self) -> None:
        # Failed container cleanup stays in active, so its mounted files remain retained.
        self.store.collect_revisions(
            {session.environment.binary_dir for session in self.sessions.active}
        )

    def update_build(self, pr: int, update: BuildUpdate) -> EnvironmentMetadata:
        # This synchronous atomic update must not wait behind a streaming upload.
        metadata = self.store.update_build(pr, update)
        self.collect_artifacts()
        return metadata

    async def cleanup_periodically(self) -> None:
        while True:
            await asyncio.sleep(self.settings.cleanup_interval_seconds)
            try:
                async with self.uploads:
                    await self.cleanup_inactive()
            except OSError, RuntimeError, TimeoutError:
                logger.exception("Failed to expire playground environments")

    async def deploy(
        self,
        chunks: AsyncIterable[bytes],
        sha: str,
        pr: int,
        build: BuildIdentity | None = None,
    ) -> Environment:
        artifact = ArtifactIdentity(sha=sha, pr=pr)
        self.store.check_build(pr, build)
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
                environment = self.store.publish(staged, clis, artifact.sha, artifact.pr, build)
                self.collect_artifacts()
                return environment
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
        return self.store.metadata(identifier)
