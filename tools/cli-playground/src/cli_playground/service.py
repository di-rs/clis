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
from functools import partial
from pathlib import Path
from uuid import UUID, uuid4

from anyio import CancelScope

from cli_playground import domain
from cli_playground.artifacts import canonical_archive, stage_archive
from cli_playground.config import Settings
from cli_playground.domain import ArtifactConflictError, CreateIntent, SessionBinding, UploadIntent
from cli_playground.models import (
    ArtifactIdentity,
    ArtifactMetadata,
    BuildIdentity,
    BuildUpdate,
    Environment,
    EnvironmentMetadata,
)
from cli_playground.observability import current_request_id, request_context, sandbox_context
from cli_playground.ports import EnvironmentStore, SessionStore
from cli_playground.sandbox import (
    BoxieClient,
    BoxieUnavailable,
    SandboxRequest,
    SandboxView,
)
from cli_playground.upload_staging import UploadStaging, run_staging

logger = logging.getLogger(__name__)


class UploadBusyError(Exception):
    """Another artifact upload is holding the deployment lock."""


class UploadTooLargeError(Exception):
    """An artifact exceeded the configured size limit."""


class CliPlaygroundService:
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
        self.staging = UploadStaging(settings.data_dir)
        self.uploads = asyncio.Semaphore(1)
        self.active_creates: set[UUID] = set()

    async def retry_stops(self) -> None:
        for binding in await self.sessions.pending_stops():
            with (
                request_context(str(binding.stop_request_id) if binding.stop_request_id else None),
                sandbox_context(binding.sandbox.id),
            ):
                try:
                    view = await self.boxie.terminate(binding.sandbox.id, binding.sandbox)
                    await self.sessions.change(
                        binding.id,
                        partial(domain.confirm_stop, view=view),
                        claimed=True,
                    )
                except BoxieUnavailable:
                    logger.warning("Sandbox stop remains pending")

    async def collect_artifacts(self) -> None:
        await self.store.collect_artifacts()

    async def retry_retirements(self) -> None:
        for identifier in await self.sessions.retirements():
            origin = await self.sessions.retirement_request_id(identifier)
            with request_context(str(origin) if origin else None):
                try:
                    await self.boxie.retire(identifier)
                    await self.sessions.retired(identifier)
                except BoxieUnavailable:
                    logger.warning("Artifact retirement remains pending")

    async def cleanup_inactive(self) -> None:
        identifiers = await self.store.inactive_identifiers()
        await self.sessions.stop_environments(identifiers)
        await self.retry_stops()
        await self.collect_artifacts()
        await self.store.remove(identifiers)
        await self.retry_retirements()

    async def update_build(self, pr: int, update: BuildUpdate) -> EnvironmentMetadata:
        metadata = await self.store.update_build(pr, update)
        await self.collect_artifacts()
        return metadata

    async def finish_upload(self, intent: UploadIntent) -> Environment:
        with request_context(str(intent.request_id) if intent.request_id else None):
            artifact = intent.artifact
            await self.boxie.upload(
                artifact.artifact_id, artifact.sha256, self.staging.archive(artifact.artifact_id)
            )
            try:
                environment = await self.store.finish_upload(artifact, intent.build)
            except ArtifactConflictError:
                self.staging.remove(artifact.artifact_id)
                raise
            self.staging.remove(artifact.artifact_id)
            await self.collect_artifacts()
            return environment

    async def recover(self, *, startup: bool = True) -> None:
        # A restarted process cannot finish an in-flight terminal attachment.
        for binding in await self.sessions.pending_attachments(
            before=None if startup else self.clock()
        ):
            await self.cancel_attachment(binding.id, claim=binding.attachment_claim)
        # A restarted process cannot return an old page's one-time credential.
        for intent in await self.sessions.creates():
            saved = await self.sessions.get(intent.id)
            if saved is not None and (
                saved.attached or saved.attachment_deadline is not None or saved.pending_stop
            ):
                continue
            if intent.id in self.active_creates or (
                not startup and not intent.abandoned and intent.claim_until > self.clock()
            ):
                continue
            with request_context(str(intent.request_id) if intent.request_id else None):
                try:
                    sandbox = await self.boxie.create(str(intent.id), intent.spec)
                    await self.sessions.recover_creation(intent, sandbox)
                except BoxieUnavailable:
                    logger.warning("Sandbox admission recovery remains pending")
        for upload_intent in await self.sessions.uploads():
            with request_context(
                str(upload_intent.request_id) if upload_intent.request_id else None
            ):
                try:
                    await self.finish_upload(upload_intent)
                except BoxieUnavailable, ArtifactConflictError:
                    logger.warning("Artifact publication recovery deferred or abandoned")
        await self.cleanup_inactive()

    async def binding(
        self, preview_id: str, identifier: UUID, revision: str, credential: str
    ) -> SessionBinding:
        binding = await self.sessions.get(identifier)
        if binding is None or await self.store.record(preview_id) is None:
            raise LookupError("session not found")
        domain.authenticate_binding(binding, preview_id, revision, credential, self.clock())
        return binding

    async def claim_attachment(
        self, preview_id: str, identifier: UUID, revision: str, credential: str
    ) -> SessionBinding:
        if await self.store.record(preview_id) is None:
            raise LookupError("session not found")

        def change(binding: SessionBinding) -> None:
            domain.authenticate_binding(binding, preview_id, revision, credential, self.clock())
            domain.claim_attachment(binding, self.clock())

        binding = await self.sessions.change(identifier, change, claimed=True)
        if binding is None:
            raise LookupError("session not found")
        return binding

    async def check_attachment_claim(
        self, preview_id: str, identifier: UUID, revision: str, credential: str, claim: int
    ) -> None:
        if (
            await self.binding(preview_id, identifier, revision, credential)
        ).attachment_claim != claim:
            raise LookupError("attachment superseded")

    async def complete_attachment(
        self,
        preview_id: str,
        identifier: UUID,
        revision: str,
        credential: str,
        view: SandboxView,
        *,
        claim: int | None = None,
    ) -> None:
        if await self.store.record(preview_id) is None:
            raise LookupError("session not found")

        def change(binding: SessionBinding) -> None:
            domain.authenticate_binding(binding, preview_id, revision, credential, self.clock())
            domain.complete_attachment(binding, view, claim)

        if await self.sessions.change(identifier, change) is None:
            raise LookupError("session not found")

    async def cancel_attachment(self, identifier: UUID, *, claim: int | None = None) -> None:
        await self.sessions.change(
            identifier,
            partial(
                domain.cancel_attachment,
                claim=claim,
                request_id=UUID(current_request_id() or str(uuid4())),
            ),
        )

    async def create_session(self, preview_id: str, revision: str) -> tuple[SessionBinding, str]:
        environment = await self.store.get(preview_id)
        if environment is None:
            raise LookupError("preview not found")
        if environment.revision != revision:
            raise ArtifactConflictError("preview revision changed")
        credential = secrets.token_urlsafe(32)
        intent = CreateIntent(
            request_id=UUID(current_request_id() or str(uuid4())),
            id=uuid4(),
            preview_id=preview_id,
            revision=revision,
            credential_hash=hashlib.sha256(credential.encode()).hexdigest(),
            claim_until=self.clock() + 30,
            spec=SandboxRequest(artifact_id=environment.artifact_id),
        )
        await self.sessions.create_intent(intent)
        self.active_creates.add(intent.id)
        try:
            with request_context(str(intent.request_id)):
                sandbox = await self.boxie.create(str(intent.id), intent.spec)
            binding = SessionBinding(
                id=intent.id,
                preview_id=preview_id,
                revision=revision,
                credential_hash=intent.credential_hash,
                sandbox=sandbox,
            )
            await self.sessions.save(binding)
            current = await self.store.get(preview_id)
            if current is None or current.revision != revision:
                domain.request_stop(binding, UUID(current_request_id() or str(uuid4())))
                await self.sessions.save(binding)
                await self.retry_stops()
                raise ArtifactConflictError("preview revision changed")
            intent.claim_until = self.clock() + 30
            await self.sessions.create_intent(intent)
            return binding, credential
        except BaseException:
            # The persisted idempotency key resolves ambiguous/cancelled admissions on recovery.
            with CancelScope(shield=True):
                saved = await self.sessions.get(intent.id)
                if (
                    saved is None
                    or saved.sandbox.cleanup_pending
                    or saved.sandbox.state not in {"terminated", "failed"}
                ):
                    intent.abandoned = True
                    await self.sessions.create_intent(intent)
                    if saved is not None:
                        await self.sessions.change(
                            saved.id,
                            partial(
                                domain.request_stop,
                                request_id=UUID(current_request_id() or str(uuid4())),
                            ),
                        )
            raise
        finally:
            self.active_creates.discard(intent.id)

    async def stop_session(self, binding: SessionBinding) -> None:
        await self.sessions.change(
            binding.id,
            partial(domain.request_stop, request_id=UUID(current_request_id() or str(uuid4()))),
        )
        await self.retry_stops()

    async def cleanup_periodically(self) -> None:
        while True:
            await asyncio.sleep(self.settings.cleanup_interval_seconds)
            with request_context(str(uuid4())):
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
        await self.store.check_build(pr, build)
        try:
            await asyncio.wait_for(self.uploads.acquire(), timeout=1)
        except TimeoutError as error:
            raise UploadBusyError from error
        temporary: Path | None = None
        try:
            async with timeout(60):
                await self.cleanup_inactive()
                temporary = self.staging.temporary_directory()
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
                    clis, digest = await run_staging(prepare)
                except OverflowError as error:
                    raise UploadTooLargeError from error
                except (zipfile.BadZipFile, zlib.error, RuntimeError) as error:
                    raise ValueError("invalid binary bundle") from error
                # Revisions may expire while the upload body is streaming.
                await self.cleanup_inactive()
                await self.store.check_build(pr, build)
                revision = sha if build is None else f"{sha}-{build.run_number}-{build.run_attempt}"
                existing = await self.store.revision(pr, revision)
                if existing is not None and (existing.sha256 != digest or existing.clis != clis):
                    raise ArtifactConflictError(
                        "this revision already has different binaries or CLI set"
                    )
                current = await self.store.get(domain.identifier(pr))
                if current is not None and current.revision == revision:
                    return current
                pending = next(
                    (
                        item
                        for item in (await self.sessions.uploads())
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
                    request_id=UUID(current_request_id() or str(uuid4())),
                    artifact=metadata,
                    build=build,
                    previous_revision=current.revision if current else None,
                )
                await run_staging(partial(self.staging.stage, metadata.artifact_id, canonical))
                try:
                    await self.sessions.upload_intent(intent)
                except ArtifactConflictError:
                    await run_staging(partial(self.staging.remove, metadata.artifact_id))
                    raise
                return await self.finish_upload(intent)
        finally:
            if temporary is not None:
                shutil.rmtree(temporary, ignore_errors=True)
            self.uploads.release()

    async def close_pull_request(self, pr: int) -> int:
        # Publish must see the tombstone even while we wait for its upload lock.
        await self.store.close_pull_request(pr)
        async with self.uploads:
            identifiers = {domain.identifier(pr)}
            await self.sessions.stop_environments(identifiers)
            await self.collect_artifacts()
            await self.retry_stops()
            removed = await self.store.remove(identifiers)
            await self.retry_retirements()
            return removed

    async def reopen_pull_request(self, pr: int) -> None:
        async with self.uploads:
            await self.store.reopen_pull_request(pr)

    async def metadata(self, identifier: str) -> EnvironmentMetadata | None:
        return await self.store.metadata(identifier)
