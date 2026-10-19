"""Persisted page bindings and replayable remote-operation intents."""

import hashlib
import os
import secrets
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from cli_playground.boxie_client import SandboxRequest, SandboxView
from cli_playground.models import ArtifactMetadata, BuildIdentity


def atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_suffix(".pending")
    with temporary.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    sync_directory(path.parent)


def sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def unlink_and_sync(path: Path) -> None:
    path.unlink(missing_ok=True)
    sync_directory(path.parent)


class SessionBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    preview_id: str
    revision: str
    sandbox: SandboxView
    credential_hash: str
    pending_stop: bool = False
    attachment_deadline: float | None = None
    attached: bool = False
    attachment_claim: int = 0

    def authenticates(self, credential: str) -> bool:
        return secrets.compare_digest(
            self.credential_hash, hashlib.sha256(credential.encode()).hexdigest()
        )


class CreateIntent(BaseModel):
    id: UUID
    preview_id: str
    revision: str
    credential_hash: str
    spec: SandboxRequest
    claim_until: float
    abandoned: bool = False


class UploadIntent(BaseModel):
    artifact: ArtifactMetadata
    build: BuildIdentity | None
    previous_revision: str | None
    cancelled: bool = False


class SessionStore:
    def __init__(self, root: Path) -> None:
        self.root = root / ".operations"
        for name in ("bindings", "creates", "uploads", "retirements"):
            (self.root / name).mkdir(parents=True, exist_ok=True, mode=0o700)

    def save(self, binding: SessionBinding) -> None:
        atomic_write(
            self.root / "bindings" / f"{binding.id}.json", binding.model_dump_json().encode()
        )

    def bindings(self) -> list[SessionBinding]:
        return [
            SessionBinding.model_validate_json(p.read_bytes())
            for p in (self.root / "bindings").glob("*.json")
        ]

    def get(self, identifier: UUID) -> SessionBinding | None:
        path = self.root / "bindings" / f"{identifier}.json"
        return SessionBinding.model_validate_json(path.read_bytes()) if path.exists() else None

    def create_intent(self, intent: CreateIntent) -> None:
        atomic_write(self.root / "creates" / f"{intent.id}.json", intent.model_dump_json().encode())

    def creates(self) -> list[CreateIntent]:
        return [
            CreateIntent.model_validate_json(p.read_bytes())
            for p in (self.root / "creates").glob("*.json")
        ]

    def claimed(self, identifier: UUID) -> None:
        unlink_and_sync(self.root / "creates" / f"{identifier}.json")

    def upload_intent(self, intent: UploadIntent, archive: Path) -> None:
        identifier = intent.artifact.artifact_id
        target = self.archive(identifier)
        # Bytes must be synced to disk before the intent can become visible to recovery.
        with archive.open("rb") as source, target.open("wb") as destination:
            import shutil

            shutil.copyfileobj(source, destination)
            destination.flush()
            os.fsync(destination.fileno())
        self.save_upload(intent)

    def save_upload(self, intent: UploadIntent) -> None:
        atomic_write(
            self.root / "uploads" / f"{intent.artifact.artifact_id}.json",
            intent.model_dump_json().encode(),
        )

    def cancel_uploads(self, pr: int) -> None:
        for intent in self.uploads():
            if intent.artifact.pr == pr and not intent.cancelled:
                intent.cancelled = True
                self.save_upload(intent)

    def uploads(self) -> list[UploadIntent]:
        return [
            UploadIntent.model_validate_json(p.read_bytes())
            for p in (self.root / "uploads").glob("*.json")
        ]

    def archive(self, identifier: UUID) -> Path:
        return self.root / "uploads" / f"{identifier}.zip"

    def uploaded(self, identifier: UUID) -> None:
        unlink_and_sync(self.root / "uploads" / f"{identifier}.json")
        unlink_and_sync(self.archive(identifier))

    def retire(self, identifier: UUID) -> None:
        if (self.root / "retirements" / f"{identifier}.done").exists():
            return
        atomic_write(self.root / "retirements" / str(identifier), b"")

    def retirements(self) -> list[UUID]:
        return [UUID(p.name) for p in (self.root / "retirements").iterdir() if not p.suffix]

    def retired(self, identifier: UUID) -> None:
        (self.root / "retirements" / str(identifier)).replace(
            self.root / "retirements" / f"{identifier}.done"
        )
        sync_directory(self.root / "retirements")

    def stop_environments(self, identifiers: set[str]) -> None:
        for binding in self.bindings():
            if (
                binding.preview_id in identifiers
                and not binding.pending_stop
                and (
                    binding.sandbox.cleanup_pending
                    or binding.sandbox.state not in {"terminated", "failed"}
                )
            ):
                binding.pending_stop = True
                self.save(binding)
