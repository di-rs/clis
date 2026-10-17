"""Bounded ZIP bundles of flat Linux executable names; no archive paths are extracted."""

import hashlib
import io
import re
import shutil
import struct
import zipfile
from pathlib import Path
from typing import BinaryIO

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


def validate_zip_directory(source: BinaryIO) -> None:
    """Scan directory records with fixed buffers before ZipFile allocates ZipInfo objects."""
    position = source.tell()
    try:
        source.seek(0, io.SEEK_END)
        size = source.tell()
        if size > MAX_ARCHIVE:
            raise ValueError("artifact exceeds size limit")
        source.seek(max(0, size - 65557))
        tail = source.read(65557)
        # EOCD's variable comment may itself contain a signature. Only a record
        # ending exactly at EOF is a candidate; never trust its count or size yet.
        index = len(tail)
        while True:
            index = tail.rfind(b"PK\x05\x06", 0, index)
            if index < 0:
                raise ValueError("invalid ZIP directory")
            if len(tail) - index >= 22 and index + 22 + struct.unpack_from("<H", tail, index + 20)[
                0
            ] == len(tail):
                break
        footer = struct.unpack_from("<4s4H2LH", tail, index)
        _, disk, directory_disk, disk_count, count, directory_size, offset, _ = footer
        end = size - len(tail) + index
        if disk or directory_disk or disk_count != count:
            raise ValueError("multi-disk ZIP is not supported")
        if end >= 20:
            source.seek(end - 20)
            locator = source.read(20)
        else:
            locator = b""
        zip64 = locator.startswith(b"PK\x06\x07")
        if zip64:
            _, disk64, zip64_offset, disks = struct.unpack("<4sLQL", locator)
            if disk64 != 0 or disks != 1 or zip64_offset + 56 > end - 20:
                raise ValueError("invalid ZIP64 locator")
            source.seek(zip64_offset)
            record = source.read(56)
            if len(record) != 56:
                raise ValueError("truncated ZIP64 directory")
            sig, length, _, _, disk64, directory_disk64, disk_count64, count64, size64, offset64 = (
                struct.unpack("<4sQ2H2L4Q", record)
            )
            if (
                sig != b"PK\x06\x06"
                or length < 44
                or length > 8 * 1024 * 1024
                or zip64_offset + length + 12 != end - 20
                or disk64
                or directory_disk64
                or disk_count64 != count64
                or count not in (65535, count64)
                or directory_size not in (0xFFFFFFFF, size64)
                or offset not in (0xFFFFFFFF, offset64)
            ):
                raise ValueError("inconsistent ZIP64 directory")
            count, directory_size, offset = count64, size64, offset64
            end = zip64_offset
        elif count == 65535 or directory_size == 0xFFFFFFFF or offset == 0xFFFFFFFF:
            raise ValueError("missing ZIP64 metadata")
        if (
            not 1 <= count <= MAX_CLIS
            or not 0 < directory_size <= 8 * 1024 * 1024
            or offset < 0
            or offset + directory_size != end
        ):
            raise ValueError("ZIP directory exceeds limits or has inconsistent offsets")
        source.seek(offset)
        actual = 0
        while source.tell() < end:
            header = source.read(46)
            if len(header) != 46 or header[:4] != b"PK\x01\x02":
                raise ValueError("invalid ZIP directory record")
            name_size, extra_size, comment_size, disk_start = struct.unpack_from("<4H", header, 28)
            actual += 1
            if (
                actual > MAX_CLIS
                or not 1 <= name_size <= 64
                or extra_size > 4096
                or comment_size > 4096
                or disk_start != 0
                or source.tell() + name_size + extra_size + comment_size > end
            ):
                raise ValueError("ZIP directory member exceeds limits")
            name = source.read(name_size)
            try:
                if re.fullmatch(CLI_PATTERN, name.decode("ascii")) is None:
                    raise ValueError("unsafe binary artifact name")
            except UnicodeError as error:
                raise ValueError("unsafe binary artifact name") from error
            source.seek(extra_size + comment_size, io.SEEK_CUR)
        if source.tell() != end or actual != count:
            raise ValueError("inconsistent ZIP directory count")
    finally:
        source.seek(position)


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
    source = io.BytesIO(data)
    validate_zip_directory(source)
    with zipfile.ZipFile(source) as archive:
        for member in archive_members(archive):
            binary = archive.read(member)
            validate_elf_header(binary[:64])
            binaries[member.filename] = binary
    return binaries


def stage_archive(source: Path, destination: Path, max_binary_bytes: int) -> list[str]:
    """Validate and stage one member at a time so large batches stay off the heap."""
    with source.open("rb") as stream:
        validate_zip_directory(stream)
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
