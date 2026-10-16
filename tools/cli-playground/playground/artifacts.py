"""Bounded ZIP bundles of flat Linux executable names; no archive paths are extracted."""

import hashlib
import io
import re
import shutil
import struct
import zipfile
from pathlib import Path

MAX_BINARY = 64 * 1024 * 1024
MAX_ARCHIVE = 512 * 1024 * 1024
MAX_CLIS = 1024
CLI_PATTERN = r"[a-z][a-z0-9_-]{0,63}"


def validate_elf_header(header: bytes) -> None:
    if len(header) < 64 or header[:7] != b"\x7fELF\x02\x01\x01":
        raise ValueError("expected a Linux x86_64 ELF executable")
    elf_type, machine, version = struct.unpack_from("<HHI", header, 16)
    header_size = struct.unpack_from("<H", header, 52)[0]
    if elf_type not in {2, 3} or machine != 62 or version != 1 or header_size != 64:
        raise ValueError("expected a Linux x86_64 ELF executable")
    if header[7] not in {0, 3}:
        raise ValueError("expected a Linux or System V ELF ABI")


def archive_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    if not 1 <= len(members) <= MAX_CLIS or sum(m.file_size for m in members) > MAX_ARCHIVE:
        raise ValueError("binary bundle exceeds limits")
    names: set[str] = set()
    for member in members:
        if (
            not re.fullmatch(CLI_PATTERN, member.filename)
            or member.filename in names
            or member.is_dir()
            or member.flag_bits & 1
            or member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
            or (member.external_attr >> 16) & 0o170000 not in (0, 0o100000)
            or not 64 <= member.file_size <= MAX_BINARY
        ):
            raise ValueError("unsafe binary artifact")
        names.add(member.filename)
    return members


def binary_archive(data: bytes) -> dict[str, bytes]:
    if len(data) > MAX_ARCHIVE:
        raise ValueError("artifact exceeds size limit")
    binaries = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for member in archive_members(archive):
            binary = archive.read(member)
            validate_elf_header(binary[:64])
            binaries[member.filename] = binary
    return binaries


def stage_archive(source: Path, destination: Path, max_binary_bytes: int) -> list[str]:
    """Validate and stage one member at a time so large batches stay off the heap."""
    with zipfile.ZipFile(source) as archive:
        members = archive_members(archive)
        if any(member.file_size > max_binary_bytes for member in members):
            raise OverflowError("binary exceeds the upload limit")
        destination.mkdir()
        for member in members:
            with (
                archive.open(member) as incoming,
                (destination / member.filename).open("wb") as out,
            ):
                header = incoming.read(64)
                validate_elf_header(header)
                out.write(header)
                shutil.copyfileobj(incoming, out, length=65536)
    return sorted(member.filename for member in members)


def canonical_archive(staged: Path, clis: list[str], destination: Path) -> str:
    """Hash deterministic bytes so ZIP timestamps cannot change publication identity."""
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED) as bundle:
        for cli in sorted(clis):
            info = zipfile.ZipInfo(cli, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o100555 << 16
            with bundle.open(info, "w") as entry, (staged / cli).open("rb") as source:
                shutil.copyfileobj(source, entry)
    if destination.stat().st_size > MAX_ARCHIVE:
        raise OverflowError("canonical artifact exceeds upload limit")
    with destination.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()
