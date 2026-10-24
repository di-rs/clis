"""Synchronous decisions and values; no database, filesystem or network operations."""

import hashlib
import secrets
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict

from cli_playground.models import (
    ArtifactMetadata,
    BuildIdentity,
    BuildStatus,
    BuildUpdate,
    PreviewRecord,
)
from cli_playground.sandbox import SandboxRequest, SandboxView


def optional_request_id(value: object) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str) or len(value) != 36:
        return None
    try:
        parsed = UUID(value)
        return parsed if str(parsed) == value else None
    except ValueError:
        return None


type RequestId = Annotated[UUID | None, BeforeValidator(optional_request_id)]


class ArtifactConflictError(ValueError):
    """A stale build or immutable revision cannot replace the current preview."""


def identifier(pr: int) -> str:
    return hashlib.sha256(f"pull-request:{pr}".encode()).hexdigest()[:32]


def check_build(record: PreviewRecord | None, closed: bool, build: BuildIdentity | None) -> None:
    if closed:
        raise ArtifactConflictError("this pull request is closed")
    current = record.build if record else None
    if current is not None:
        if build is None:
            raise ArtifactConflictError("build identity is required")
        if build.order < current.order:
            raise ArtifactConflictError("stale build")
        if build.order == current.order and (
            build.sha != current.sha or build.run_id != current.run_id
        ):
            raise ArtifactConflictError("build identity conflict")


def next_build(
    record: PreviewRecord | None, pr: int, update: BuildUpdate, expires_at: float
) -> PreviewRecord:
    if record is not None and record.build is not None and update.order == record.build.order:
        if record.build.state == "ready" or (
            record.build.state != "building" and update.state == "building"
        ):
            return record
    return PreviewRecord(
        pr=pr,
        expires_at=expires_at,
        build=BuildStatus(**update.model_dump()),
        artifact=record.artifact if record is not None and update.state != "empty" else None,
    )


def check_revision(
    previous: ArtifactMetadata | None, artifact: ArtifactMetadata, build: BuildIdentity | None
) -> None:
    if build is not None and build.sha != artifact.sha:
        raise ArtifactConflictError("build identity conflict")
    if previous is not None and (
        previous.sha256 != artifact.sha256
        or previous.clis != artifact.clis
        or previous.catalog_sha256 != artifact.catalog_sha256
    ):
        raise ArtifactConflictError("this revision already has different binaries or CLI set")


class SessionBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    preview_id: str
    revision: str
    sandbox: SandboxView
    credential_hash: str
    pending_stop: bool = False
    stop_request_id: RequestId = None
    attachment_deadline: float | None = None
    attached: bool = False
    attachment_claim: int = 0

    def authenticates(self, credential: str) -> bool:
        return secrets.compare_digest(
            self.credential_hash, hashlib.sha256(credential.encode()).hexdigest()
        )


class CreateIntent(BaseModel):
    request_id: RequestId = None
    id: UUID
    preview_id: str
    revision: str
    credential_hash: str
    spec: SandboxRequest
    claim_until: float
    abandoned: bool = False


class UploadIntent(BaseModel):
    request_id: RequestId = None
    artifact: ArtifactMetadata
    build: BuildIdentity | None
    previous_revision: str | None
    cancelled: bool = False


def authenticate_binding(
    binding: SessionBinding, preview_id: str, revision: str, credential: str, now: float
) -> None:
    from datetime import UTC, datetime

    if (
        binding.preview_id != preview_id
        or binding.revision != revision
        or not binding.authenticates(credential)
        or binding.pending_stop
        or binding.sandbox.state in {"terminating", "terminated", "failed"}
        or binding.sandbox.absolute_deadline <= datetime.fromtimestamp(now, UTC)
    ):
        raise LookupError("session not found")


def claim_attachment(binding: SessionBinding, now: float) -> None:
    binding.attachment_claim += 1
    binding.attachment_deadline = now + 40


def complete_attachment(binding: SessionBinding, view: SandboxView, claim: int | None) -> None:
    from cli_playground.sandbox import validate_sandbox

    if claim is not None and claim != binding.attachment_claim:
        raise LookupError("attachment superseded")
    validate_sandbox(view, binding.sandbox)
    binding.sandbox = view
    binding.attachment_deadline = None
    binding.attached = True


def cancel_attachment(
    binding: SessionBinding, claim: int | None, request_id: UUID | None = None
) -> None:
    if binding.attachment_deadline is not None and (
        claim is None or claim == binding.attachment_claim
    ):
        if not binding.attached:
            request_stop(binding, request_id)
        else:
            binding.pending_stop = False
        binding.attachment_deadline = None


def request_stop(binding: SessionBinding, request_id: UUID | None = None) -> None:
    if binding.sandbox.state in {"terminated", "failed"} and not binding.sandbox.cleanup_pending:
        return
    if not binding.pending_stop:
        binding.stop_request_id = request_id
    binding.pending_stop = True


def confirm_stop(binding: SessionBinding, view: SandboxView) -> None:
    from cli_playground.sandbox import validate_sandbox

    validate_sandbox(view, binding.sandbox)
    binding.sandbox = view
    binding.attachment_deadline = None
    binding.pending_stop = view.cleanup_pending or view.state not in {"terminated", "failed"}


def publication(
    record: PreviewRecord | None,
    closed: bool,
    previous: ArtifactMetadata | None,
    artifact: ArtifactMetadata,
    build: BuildIdentity | None,
    now: float,
) -> PreviewRecord:
    check_build(record, closed, build)
    check_revision(previous, artifact, build)
    if (
        record
        and record.artifact
        and record.artifact.revision == artifact.revision
        and record.artifact.expires_at > now
    ):
        return record
    return PreviewRecord(
        pr=artifact.pr,
        expires_at=artifact.expires_at,
        artifact=artifact,
        build=BuildStatus(**build.model_dump(), state="ready") if build else None,
    )


def check_upload(
    intent: UploadIntent, record: PreviewRecord | None, closed: bool, now: float
) -> None:
    if intent.cancelled:
        raise ArtifactConflictError("publication was cancelled by PR closure")
    active = (
        record.artifact
        if record
        and record.artifact
        and not closed
        and record.expires_at > now
        and record.artifact.expires_at > now
        else None
    )
    if intent.build is None and (active.revision if active else None) not in {
        intent.previous_revision,
        intent.artifact.revision,
    }:
        raise ArtifactConflictError("a newer publication superseded this upload")
