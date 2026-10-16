"""Artifact and sandbox lifecycle, independent of the HTTP transport."""

import asyncio
import hashlib
import logging
import secrets
import shutil
import time
import zipfile
import zlib
from asyncio import timeout
from collections.abc import AsyncIterable, Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from anyio import CancelScope

from playground.artifacts import canonical_archive, stage_archive
from playground.boxie_client import (
    BoxieClient,
    BoxieUnavailable,
    SandboxRequest,
    SandboxView,
    validate_sandbox,
)
from playground.config import Settings
from playground.models import (
    ArtifactIdentity,
    ArtifactMetadata,
    BuildIdentity,
    BuildUpdate,
    Environment,
    EnvironmentMetadata,
)
from playground.session_store import CreateIntent, SessionBinding, SessionStore, UploadIntent
from playground.storage import ArtifactConflictError, EnvironmentStore

logger = logging.getLogger(__name__)


class UploadBusyError(Exception):
    """Another artifact upload is holding the deployment lock."""


class UploadTooLargeError(Exception):
    """An artifact exceeded the configured size limit."""


class PlaygroundService:
    def __init__(
        self,
        settings: Settings,
        store: EnvironmentStore,
        sessions: SessionStore,
        boxie: BoxieClient,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings, self.store, self.sessions, self.boxie = settings, store, sessions, boxie
        self.clock = clock
        self.uploads = asyncio.Semaphore(1)
        self.active_creates: set[UUID] = set()

    async def retry_stops(self) -> None:
        for binding in self.sessions.bindings():
            if binding.pending_stop:
                try:
                    view = await self.boxie.terminate(binding.sandbox.id, binding.sandbox)
                    validate_sandbox(view, binding.sandbox)
                    binding.sandbox = view
                    binding.attachment_deadline = None
                    binding.pending_stop = (
                        binding.sandbox.cleanup_pending
                        or binding.sandbox.state not in {"terminated", "failed"}
                    )
                    self.sessions.save(binding)
                    self.sessions.claimed(binding.id)
                except BoxieUnavailable:
                    logger.warning("Sandbox stop remains pending")

    def collect_artifacts(self) -> None:
        pending = {intent.artifact.artifact_id for intent in self.sessions.uploads()}
        for artifact in self.store.revisions():
            current = self.store.get(self.store.identifier(artifact.pr))
            if artifact.artifact_id not in pending and (
                current is None or current.artifact_id != artifact.artifact_id
            ):
                self.sessions.retire(artifact.artifact_id)

    async def retry_retirements(self) -> None:
        for identifier in self.sessions.retirements():
            try:
                await self.boxie.retire(identifier)
                self.sessions.retired(identifier)
            except BoxieUnavailable:
                logger.warning("Artifact retirement remains pending")

    async def cleanup_inactive(self) -> None:
        identifiers = self.store.inactive_identifiers()
        identifiers.update(
            binding.preview_id
            for binding in self.sessions.bindings()
            if self.store.record(binding.preview_id) is None
        )
        self.sessions.stop_environments(identifiers)
        await self.retry_stops()
        self.collect_artifacts()
        self.store.remove(
            {identifier for identifier in identifiers if self.store.record(identifier) is None}
        )
        await self.retry_retirements()

    def update_build(self, pr: int, update: BuildUpdate) -> EnvironmentMetadata:
        metadata = self.store.update_build(pr, update)
        self.collect_artifacts()
        return metadata

    async def finish_upload(self, intent: UploadIntent) -> Environment:
        artifact = intent.artifact
        try:
            await self.boxie.upload(
                artifact.artifact_id, artifact.sha256, self.sessions.archive(artifact.artifact_id)
            )
            # Closure cancellation is irreversible even if the PR has since reopened.
            stored = next(
                item
                for item in self.sessions.uploads()
                if item.artifact.artifact_id == artifact.artifact_id
            )
            if stored.cancelled:
                raise ArtifactConflictError("publication was cancelled by PR closure")
            current = self.store.get(self.store.identifier(artifact.pr))
            if intent.build is None and (current.revision if current else None) not in {
                intent.previous_revision,
                artifact.revision,
            }:
                raise ArtifactConflictError("a newer publication superseded this upload")
            # No await between the final stale/closed check and atomic publication.
            environment = self.store.publish(artifact, intent.build)
        except ArtifactConflictError:
            self.sessions.retire(artifact.artifact_id)
            self.sessions.uploaded(artifact.artifact_id)
            raise
        self.sessions.uploaded(artifact.artifact_id)
        self.collect_artifacts()
        return environment

    async def recover(self, *, startup: bool = True) -> None:
        # A restarted process cannot finish an in-flight terminal attachment.
        for binding in self.sessions.bindings():
            if binding.attachment_deadline is not None and (
                startup or binding.attachment_deadline <= self.clock()
            ):
                self.cancel_attachment(binding.id)
        # A restarted process cannot return an old page's one-time credential.
        for intent in self.sessions.creates():
            saved = self.sessions.get(intent.id)
            if saved is not None and (
                saved.attached or saved.attachment_deadline is not None or saved.pending_stop
            ):
                continue
            if intent.id in self.active_creates or (
                not startup and not intent.abandoned and intent.claim_until > self.clock()
            ):
                continue
            try:
                sandbox = await self.boxie.create(str(intent.id), intent.spec)
                if saved is not None:
                    validate_sandbox(sandbox, saved.sandbox)
                if not any(current.id == intent.id for current in self.sessions.creates()):
                    continue  # Authentication claimed the binding while recovery yielded.
                self.sessions.save(
                    SessionBinding(
                        id=intent.id,
                        preview_id=intent.preview_id,
                        revision=intent.revision,
                        credential_hash=intent.credential_hash,
                        sandbox=sandbox,
                        pending_stop=True,
                    )
                )
            except BoxieUnavailable:
                logger.warning("Sandbox admission recovery remains pending")
        for upload_intent in self.sessions.uploads():
            try:
                await self.finish_upload(upload_intent)
            except BoxieUnavailable, ArtifactConflictError:
                logger.warning("Artifact publication recovery deferred or abandoned")
        await self.cleanup_inactive()

    def binding(
        self, preview_id: str, identifier: UUID, revision: str, credential: str
    ) -> SessionBinding:
        binding = self.sessions.get(identifier)
        if (
            binding is None
            or binding.preview_id != preview_id
            or binding.revision != revision
            or not binding.authenticates(credential)
            or binding.pending_stop
            or binding.sandbox.state in {"terminating", "terminated", "failed"}
            or binding.sandbox.absolute_deadline <= datetime.fromtimestamp(self.clock(), UTC)
            or self.store.record(preview_id) is None
        ):
            raise LookupError("session not found")
        return binding

    def claim_attachment(
        self, preview_id: str, identifier: UUID, revision: str, credential: str
    ) -> SessionBinding:
        binding = self.binding(preview_id, identifier, revision, credential)
        binding.attachment_claim += 1
        binding.attachment_deadline = self.clock() + 40
        self.sessions.save(binding)
        self.sessions.claimed(identifier)
        return binding

    def check_attachment_claim(
        self, preview_id: str, identifier: UUID, revision: str, credential: str, claim: int
    ) -> None:
        if self.binding(preview_id, identifier, revision, credential).attachment_claim != claim:
            raise LookupError("attachment superseded")

    def complete_attachment(
        self,
        preview_id: str,
        identifier: UUID,
        revision: str,
        credential: str,
        view: SandboxView,
        *,
        claim: int | None = None,
    ) -> None:
        binding = self.binding(preview_id, identifier, revision, credential)
        if claim is not None and claim != binding.attachment_claim:
            raise LookupError("attachment superseded")
        validate_sandbox(view, binding.sandbox)
        binding.sandbox = view
        binding.attachment_deadline = None
        binding.attached = True
        self.sessions.save(binding)

    def cancel_attachment(self, identifier: UUID, *, claim: int | None = None) -> None:
        binding = self.sessions.get(identifier)
        if binding is not None and binding.attachment_deadline is not None:
            if claim is not None and claim != binding.attachment_claim:
                return
            binding.pending_stop = not binding.attached
            binding.attachment_deadline = None
            self.sessions.save(binding)

    async def create_session(self, preview_id: str, revision: str) -> tuple[SessionBinding, str]:
        environment = self.store.get(preview_id)
        if environment is None:
            raise LookupError("preview not found")
        if environment.revision != revision:
            raise ArtifactConflictError("preview revision changed")
        credential = secrets.token_urlsafe(32)
        intent = CreateIntent(
            id=uuid4(),
            preview_id=preview_id,
            revision=revision,
            credential_hash=hashlib.sha256(credential.encode()).hexdigest(),
            claim_until=self.clock() + 30,
            spec=SandboxRequest(
                artifact_id=environment.artifact_id, profile_id=self.settings.boxie_profile
            ),
        )
        self.sessions.create_intent(intent)
        self.active_creates.add(intent.id)
        try:
            sandbox = await self.boxie.create(str(intent.id), intent.spec)
            binding = SessionBinding(
                id=intent.id,
                preview_id=preview_id,
                revision=revision,
                credential_hash=intent.credential_hash,
                sandbox=sandbox,
            )
            self.sessions.save(binding)
            current = self.store.get(preview_id)
            if current is None or current.revision != revision:
                binding.pending_stop = True
                self.sessions.save(binding)
                await self.retry_stops()
                raise ArtifactConflictError("preview revision changed")
            intent.claim_until = self.clock() + 30
            self.sessions.create_intent(intent)
            return binding, credential
        except BaseException:
            # The durable idempotency key resolves ambiguous/cancelled admissions on recovery.
            saved = self.sessions.get(intent.id)
            if (
                saved is None
                or saved.sandbox.cleanup_pending
                or saved.sandbox.state not in {"terminated", "failed"}
            ):
                intent.abandoned = True
                self.sessions.create_intent(intent)
                if saved is not None:
                    saved.pending_stop = True
                    self.sessions.save(saved)
            raise
        finally:
            self.active_creates.discard(intent.id)

    async def stop_session(self, binding: SessionBinding) -> None:
        binding.pending_stop = True
        self.sessions.save(binding)
        await self.retry_stops()

    async def cleanup_periodically(self) -> None:
        while True:
            await asyncio.sleep(self.settings.cleanup_interval_seconds)
            try:
                async with self.uploads:
                    await self.recover(startup=False)
            except OSError, RuntimeError, TimeoutError:
                logger.exception("Failed to expire playground environments")

    async def deploy(
        self,
        chunks: AsyncIterable[bytes],
        sha: str,
        pr: int,
        build: BuildIdentity | None = None,
    ) -> Environment:
        ArtifactIdentity(sha=sha, pr=pr)
        self.store.check_build(pr, build)
        try:
            await asyncio.wait_for(self.uploads.acquire(), timeout=1)
        except TimeoutError as error:
            raise UploadBusyError from error
        temporary: Path | None = None
        try:
            async with timeout(60):
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
                canonical = temporary / "canonical.zip"

                def prepare() -> tuple[list[str], str]:
                    clis = stage_archive(archive, staged, self.settings.max_binary_bytes)
                    return clis, canonical_archive(staged, clis, canonical)

                try:
                    staging = asyncio.create_task(asyncio.to_thread(prepare))
                    try:
                        clis, digest = await asyncio.shield(staging)
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
                self.store.check_build(pr, build)
                revision = sha if build is None else f"{sha}-{build.run_number}-{build.run_attempt}"
                existing = self.store.revision(pr, revision)
                if existing is not None and (existing.sha256 != digest or existing.clis != clis):
                    raise ArtifactConflictError(
                        "this revision already has different binaries or CLI set"
                    )
                current = self.store.get(self.store.identifier(pr))
                if current is not None and current.revision == revision:
                    return current
                pending = next(
                    (
                        item
                        for item in self.sessions.uploads()
                        if item.artifact.pr == pr and item.artifact.revision == revision
                    ),
                    None,
                )
                if pending is not None:
                    if pending.artifact.sha256 != digest:
                        raise ArtifactConflictError("this revision has a different pending upload")
                    return await self.finish_upload(pending)
                metadata = ArtifactMetadata(
                    sha=sha,
                    pr=pr,
                    revision=revision,
                    clis=clis,
                    artifact_id=uuid4(),
                    sha256=digest,
                    expires_at=self.clock() + self.settings.preview_seconds,
                )
                intent = UploadIntent(
                    artifact=metadata,
                    build=build,
                    previous_revision=current.revision if current else None,
                )
                self.sessions.upload_intent(intent, canonical)
                return await self.finish_upload(intent)
        finally:
            if temporary is not None:
                shutil.rmtree(temporary, ignore_errors=True)
            self.uploads.release()

    async def close_pull_request(self, pr: int) -> int:
        # Publish must see the tombstone even while we wait for its upload lock.
        self.store.close_pull_request(pr)
        self.sessions.cancel_uploads(pr)
        async with self.uploads:
            identifiers = self.store.for_pull_request(pr)
            identifiers.add(self.store.identifier(pr))
            self.sessions.stop_environments(identifiers)
            self.collect_artifacts()
            await self.retry_stops()
            removed = self.store.remove(identifiers)
            await self.retry_retirements()
            return removed

    async def reopen_pull_request(self, pr: int) -> None:
        async with self.uploads:
            if self.store.is_closed(pr):
                # Also completes invalidation if close was interrupted after its tombstone.
                self.sessions.cancel_uploads(pr)
            self.store.reopen_pull_request(pr)

    async def metadata(self, identifier: str) -> EnvironmentMetadata | None:
        return self.store.metadata(identifier)
