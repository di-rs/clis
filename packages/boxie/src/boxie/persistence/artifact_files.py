"""Generic bounded ZIP extraction shared by supervisor-owned storage implementations."""

import asyncio
import os
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from boxie.domain.errors import BoxieError
from boxie.persistence.atomic import sync_directory
from boxie.persistence.zip_metadata import validate_directory

if TYPE_CHECKING:
    from boxie.persistence.artifacts import ArtifactLimits


async def extract_archive(staged: Path, limits: ArtifactLimits) -> None:
    target = staged / "files"
    target.mkdir(mode=0o755)
    names: set[str] = set()
    expanded, files = 0, 0
    await validate_directory(staged / "archive.zip", limits.files * 2)
    with zipfile.ZipFile(staged / "archive.zip") as archive:
        for member in archive.infolist():
            name = member.orig_filename
            path = PurePosixPath(name)
            normalized = str(path)
            mode = member.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if (
                not name
                or not path.parts
                or "\x00" in name
                or "\\" in name
                or ":" in path.parts[0]
                or path.is_absolute()
                or ".." in path.parts
                or normalized == "."
                or normalized in names
                or member.flag_bits & 1
                or member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                or kind not in (0, stat.S_IFREG, stat.S_IFDIR)
                or (kind == stat.S_IFDIR and not member.is_dir())
            ):
                raise BoxieError(422, "unsafe ZIP member")
            names.add(normalized)
            destination = target.joinpath(*path.parts)
            if member.is_dir():
                destination.mkdir(parents=True, exist_ok=True, mode=0o755)
                continue
            files += 1
            if files > limits.files or member.file_size > limits.member_bytes:
                raise BoxieError(413, "artifact member limit exceeded")
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            member_bytes = 0
            with archive.open(member) as source, destination.open("xb") as output:
                while chunk := source.read(64 * 1024):
                    member_bytes += len(chunk)
                    expanded += len(chunk)
                    if member_bytes > limits.member_bytes or expanded > limits.expanded_bytes:
                        raise BoxieError(413, "expanded artifact limit exceeded")
                    output.write(chunk)
                    await asyncio.sleep(0)
                output.flush()
                os.fsync(output.fileno())
            # Artifacts can contain scripts/binaries; never carry setuid or writable bits.
            destination.chmod(0o555)
    if files == 0:
        raise BoxieError(422, "artifact contains no regular files")
    sync_directory(target)
