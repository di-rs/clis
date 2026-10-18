"""Bound central-directory allocation before allowing the eager ZIP parser to run."""

import asyncio
import os
import struct
from pathlib import Path
from typing import BinaryIO

from pydantic import BaseModel, ConfigDict, Field

from boxie.domain.errors import BoxieError

MAX_DIRECTORY_BYTES = 4 * 1024 * 1024


class Directory(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    entries: int = Field(ge=0)
    size: int = Field(ge=0)
    offset: int = Field(ge=0)
    end: int = Field(ge=0)


def _directory(source: BinaryIO) -> Directory:
    # EOCD has a fixed 22-byte header and at most a 65535-byte archive comment.
    source.seek(0, os.SEEK_END)
    length = source.tell()
    tail_start = max(0, length - 65535 - 22)
    source.seek(tail_start)
    tail = source.read(65535 + 22)
    marker = tail.rfind(b"PK\x05\x06")
    if marker < 0 or len(tail) - marker < 22:
        raise BoxieError(422, "missing ZIP end record")
    _, disk, central_disk, disk_entries, entries, size, offset, comment_size = struct.unpack_from(
        "<4s4H2IH", tail, marker
    )
    end = tail_start + marker
    if marker + 22 + comment_size != len(tail):
        raise BoxieError(422, "invalid ZIP comment length")
    if end >= 20:
        source.seek(end - 20)
        locator = source.read(20)
        if locator.startswith(b"PK\x06\x07"):
            _, locator_disk, record_offset, disks = struct.unpack("<4sIQI", locator)
            if locator_disk != 0 or disks != 1 or record_offset > end - 76:
                raise BoxieError(422, "invalid ZIP64 locator")
            source.seek(record_offset)
            record = source.read(56)
            if len(record) != 56 or not record.startswith(b"PK\x06\x06"):
                raise BoxieError(422, "invalid ZIP64 end record")
            (
                _,
                record_size,
                _made_by,
                _needed,
                disk,
                central_disk,
                disk_entries,
                entries,
                size,
                offset,
            ) = struct.unpack("<4sQHHIIQQQQ", record)
            if record_size < 44 or record_offset + record_size + 12 != end - 20:
                raise BoxieError(422, "invalid ZIP64 end record size")
            # Match the parser's ZIP64 region, including its offset consistency check.
            if offset + size != record_offset:
                raise BoxieError(422, "invalid ZIP64 directory offset")
            end = record_offset
    if disk != 0 or central_disk != 0 or disk_entries != entries:
        raise BoxieError(422, "split ZIP archives are not supported")
    return Directory(entries=entries, size=size, offset=offset, end=end)


async def validate_directory(path: Path, max_entries: int) -> None:
    """Read fixed-size headers and count actual records, never materializing names/extras.

    The byte ceiling bounds the region that ZipFile will eagerly read; scanning its
    complete contents prevents a forged EOCD entry count from bypassing the entry
    ceiling. ZIP64 overrides are resolved with fixed-size reads before either cap.
    """
    with path.open("rb") as source:
        directory = _directory(source)
        if directory.size > MAX_DIRECTORY_BYTES:
            raise BoxieError(413, "ZIP central directory is too large")
        if directory.entries > max_entries:
            raise BoxieError(413, "too many archive members")
        # ZipFile infers prepended data from EOCD location, size and relative offset.
        # Scan exactly that same physical region, not an attacker-selected earlier one.
        start = directory.end - directory.size
        if start < 0 or directory.offset > start:
            raise BoxieError(422, "invalid ZIP directory offset")
        source.seek(start)
        count = 0
        while source.tell() < directory.end:
            header = source.read(min(46, directory.end - source.tell()))
            if len(header) != 46 or not header.startswith(b"PK\x01\x02"):
                raise BoxieError(422, "invalid ZIP directory member")
            count += 1
            if count > max_entries:
                raise BoxieError(413, "too many archive members")
            name_size, extra_size, comment_size = struct.unpack_from("<HHH", header, 28)
            next_offset = source.tell() + name_size + extra_size + comment_size
            if next_offset > directory.end:
                raise BoxieError(422, "truncated ZIP directory member")
            source.seek(next_offset)
            if count % 64 == 0:
                await asyncio.sleep(0)
        if count != directory.entries:
            raise BoxieError(422, "ZIP directory count mismatch")
