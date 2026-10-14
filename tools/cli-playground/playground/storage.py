"""One stable preview per PR, pointing atomically at a validated immutable batch."""

import hashlib
import re
import shutil
import tempfile
import time
from pathlib import Path

from playground.artifacts import validate_elf_header
from playground.config import Settings
from playground.models import ID_PATTERN, ArtifactMetadata, Environment


class ArtifactConflictError(ValueError):
    """A published revision cannot be replaced with different executable bytes."""


def validate_elf(path: Path) -> None:
    with path.open("rb") as stream:
        validate_elf_header(stream.read(64))


class EnvironmentStore:
    def __init__(self, settings: Settings) -> None:
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
        # Startup only: request handling has not begun, so none can still be active.
        for directory in self.root.glob(".upload-*"):
            shutil.rmtree(directory)

    def is_closed(self, pr: int) -> bool:
        return (self.closed / str(pr)).exists()

    def close_pull_request(self, pr: int) -> None:
        (self.closed / str(pr)).touch(mode=0o600)

    def reopen_pull_request(self, pr: int) -> None:
        (self.closed / str(pr)).unlink(missing_ok=True)

    def get(self, identifier: str, *, include_inactive: bool = False) -> Environment | None:
        if re.fullmatch(ID_PATTERN, identifier) is None:
            return None
        directory = self.root / identifier
        try:
            data = ArtifactMetadata.model_validate_json(
                (directory / "metadata.json").read_text(encoding="utf-8")
            )
            if self.identifier(data.pr) != identifier:
                return None
            if not include_inactive and (data.expires_at <= time.time() or self.is_closed(data.pr)):
                return None
            binary_dir = directory / data.sha
            if not all((binary_dir / cli).is_file() for cli in data.clis):
                return None
            return Environment(id=identifier, binary_dir=binary_dir, **data.model_dump())
        except OSError, ValueError:
            return None

    def inactive_identifiers(self) -> set[str]:
        return {
            directory.name
            for directory in self.root.iterdir()
            if directory.is_dir()
            and re.fullmatch(ID_PATTERN, directory.name)
            and self.get(directory.name) is None
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

    def publish(self, temporary: Path, clis: list[str], sha: str, pr: int) -> Environment:
        if self.is_closed(pr):
            raise ArtifactConflictError("this pull request is closed")
        metadata = ArtifactMetadata(
            clis=clis, sha=sha, pr=pr, expires_at=time.time() + self.settings.preview_seconds
        )
        for cli in metadata.clis:
            validate_elf(temporary / cli)
        identifier = self.identifier(pr)
        existing = self.get(identifier)
        if existing is not None and existing.sha == sha:
            if existing.clis != metadata.clis:
                raise ArtifactConflictError("this revision already has a different CLI set")
            for cli in metadata.clis:
                with (
                    (existing.binary_dir / cli).open("rb") as previous,
                    (temporary / cli).open("rb") as incoming,
                ):
                    if (
                        hashlib.file_digest(previous, "sha256").digest()
                        != hashlib.file_digest(incoming, "sha256").digest()
                    ):
                        raise ArtifactConflictError("this revision already has a different binary")
            return existing
        directory = self.root / identifier
        directory.mkdir(exist_ok=True)
        destination = directory / sha
        # A crash after moving a batch but before replacing metadata can leave this orphan.
        if destination.exists():
            shutil.rmtree(destination)
        for cli in metadata.clis:
            (temporary / cli).chmod(0o555)
        temporary.chmod(0o755)
        manifest = directory / ".metadata-upload"
        manifest.write_text(metadata.model_dump_json(), encoding="utf-8")
        try:
            temporary.rename(destination)
            manifest.replace(directory / "metadata.json")
        except OSError:
            shutil.rmtree(destination, ignore_errors=True)
            raise
        finally:
            manifest.unlink(missing_ok=True)
        # The service has closed sessions from the previous revision before this swap.
        for old_revision in directory.iterdir():
            if old_revision.is_dir() and old_revision != destination:
                shutil.rmtree(old_revision, ignore_errors=True)
        return Environment(id=identifier, binary_dir=destination, **metadata.model_dump())
