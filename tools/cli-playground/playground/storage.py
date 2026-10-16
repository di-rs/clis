"""Atomic PR build state and immutable batches, retained while sessions reference them."""

import hashlib
import os
import re
import shutil
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from playground.artifacts import validate_elf_header
from playground.config import Settings
from playground.models import (
    ID_PATTERN,
    ArtifactMetadata,
    BuildIdentity,
    BuildStatus,
    BuildUpdate,
    Environment,
    EnvironmentMetadata,
    PreviewRecord,
)


class ArtifactConflictError(ValueError):
    """A stale build or immutable revision cannot replace the current preview."""


def validate_elf(path: Path) -> None:
    with path.open("rb") as stream:
        validate_elf_header(stream.read(64))


class EnvironmentStore:
    def __init__(self, settings: Settings, *, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self.settings = settings
        self.root = settings.data_dir
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.closed = self.root / ".closed-pull-requests"
        self.closed.mkdir(exist_ok=True, mode=0o700)

    @staticmethod
    def identifier(pr: int) -> str:
        return hashlib.sha256(f"pull-request:{pr}".encode()).hexdigest()[:32]

    def temporary_directory(self) -> Path:
        return Path(tempfile.mkdtemp(prefix=".upload-", dir=self.root))

    def remove_abandoned_uploads(self) -> None:
        for directory in self.root.glob(".upload-*"):
            shutil.rmtree(directory)

    def is_closed(self, pr: int) -> bool:
        return (self.closed / str(pr)).exists()

    def close_pull_request(self, pr: int) -> None:
        from playground.session_store import atomic_write

        atomic_write(self.closed / str(pr), b"")

    def reopen_pull_request(self, pr: int) -> None:
        from playground.session_store import durable_unlink

        durable_unlink(self.closed / str(pr))

    def record(self, identifier: str, *, include_inactive: bool = False) -> PreviewRecord | None:
        if re.fullmatch(ID_PATTERN, identifier) is None:
            return None
        try:
            data = (self.root / identifier / "metadata.json").read_text(encoding="utf-8")
            record = PreviewRecord.model_validate_json(data)
            if self.identifier(record.pr) != identifier:
                return None
            if record.artifact is not None and record.artifact.pr != record.pr:
                return None
            if not include_inactive and (
                record.expires_at <= self.clock() or self.is_closed(record.pr)
            ):
                return None
            return record
        except OSError, ValueError, TypeError:
            return None

    def get(self, identifier: str, *, include_inactive: bool = False) -> Environment | None:
        record = self.record(identifier, include_inactive=include_inactive)
        if record is None or record.artifact is None:
            return None
        artifact = record.artifact
        if not include_inactive and artifact.expires_at <= self.clock():
            return None
        return Environment(id=identifier, **artifact.model_dump())

    def metadata(self, identifier: str) -> EnvironmentMetadata | None:
        record = self.record(identifier)
        if record is None:
            return None
        environment = self.get(identifier)
        return EnvironmentMetadata(
            id=identifier,
            pr=record.pr,
            sha=environment.sha if environment else None,
            revision=environment.revision if environment else None,
            clis=environment.clis if environment else [],
            expires_at=datetime.fromtimestamp(
                environment.expires_at if environment else record.expires_at, UTC
            ).isoformat(),
            build=record.build,
        )

    def inactive_identifiers(self) -> set[str]:
        return {
            directory.name
            for directory in self.root.iterdir()
            if directory.is_dir()
            and re.fullmatch(ID_PATTERN, directory.name)
            and self.record(directory.name) is None
        }

    def remove(self, identifiers: set[str]) -> int:
        deleted = 0
        for identifier in identifiers:
            directory = self.root / identifier
            if re.fullmatch(ID_PATTERN, identifier) and directory.is_dir():
                shutil.rmtree(directory)
                deleted += 1
        return deleted

    def for_pull_request(self, pr: int) -> set[str]:
        identifier = self.identifier(pr)
        return {identifier} if (self.root / identifier).is_dir() else set()

    def check_build(self, pr: int, build: BuildIdentity | None) -> PreviewRecord | None:
        if self.is_closed(pr):
            raise ArtifactConflictError("this pull request is closed")
        record = self.record(self.identifier(pr), include_inactive=True)
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
        return record

    def _write_record(self, record: PreviewRecord) -> None:
        directory = self.root / self.identifier(record.pr)
        directory.mkdir(exist_ok=True)
        manifest = directory / ".metadata-upload"
        try:
            with manifest.open("w", encoding="utf-8") as stream:
                stream.write(record.model_dump_json())
                stream.flush()
                os.fsync(stream.fileno())
            manifest.replace(directory / "metadata.json")
            descriptor = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            manifest.unlink(missing_ok=True)

    def update_build(self, pr: int, update: BuildUpdate) -> EnvironmentMetadata:
        record = self.check_build(pr, update)
        if record is not None and record.build is not None and update.order == record.build.order:
            # A delayed start/failure event must not undo a terminal result or publication.
            if record.build.state == "ready" or (
                record.build.state != "building" and update.state == "building"
            ):
                result = self.metadata(self.identifier(pr))
                if result is not None:
                    return result
        next_record = PreviewRecord(
            pr=pr,
            expires_at=self.clock() + self.settings.preview_seconds,
            build=BuildStatus(**update.model_dump()),
            artifact=record.artifact if record is not None and update.state != "empty" else None,
        )
        self._write_record(next_record)
        result = self.metadata(self.identifier(pr))
        assert result is not None
        return result

    def revisions(self) -> list[ArtifactMetadata]:
        return [
            ArtifactMetadata.model_validate_json(path.read_bytes())
            for path in self.root.glob("*/revisions/*.json")
        ]

    def revision(self, pr: int, revision: str) -> ArtifactMetadata | None:
        path = self.root / self.identifier(pr) / "revisions" / f"{revision}.json"
        return ArtifactMetadata.model_validate_json(path.read_bytes()) if path.exists() else None

    def publish(self, artifact: ArtifactMetadata, build: BuildIdentity | None) -> Environment:
        from playground.session_store import atomic_write

        self.check_build(artifact.pr, build)
        if build is not None and build.sha != artifact.sha:
            raise ArtifactConflictError("build identity conflict")
        previous = self.revision(artifact.pr, artifact.revision)
        if previous is not None and (
            previous.sha256 != artifact.sha256 or previous.clis != artifact.clis
        ):
            raise ArtifactConflictError("this revision already has different binaries or CLI set")
        existing = self.get(self.identifier(artifact.pr))
        if existing is not None and existing.revision == artifact.revision:
            return existing
        directory = self.root / self.identifier(artifact.pr) / "revisions"
        directory.mkdir(parents=True, exist_ok=True)
        atomic_write(directory / f"{artifact.revision}.json", artifact.model_dump_json().encode())
        self._write_record(
            PreviewRecord(
                pr=artifact.pr,
                expires_at=artifact.expires_at,
                artifact=artifact,
                build=BuildStatus(**build.model_dump(), state="ready") if build else None,
            )
        )
        return Environment(id=self.identifier(artifact.pr), **artifact.model_dump())
