"""Hand-built malformed metadata must be rejected before ZipFile allocation."""

import io
import struct
import zipfile
from pathlib import Path

import pytest
from playground import artifacts

from .helpers import binary_bundle, elf_binary


@pytest.mark.parametrize("fault", ["count", "extra", "comment", "name", "offset", "disk", "zip64"])
def test_metadata_rejected_before_zipfile_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    data = bytearray(binary_bundle({"echor": elf_binary()}))
    directory = data.index(b"PK\x01\x02")
    footer = data.index(b"PK\x05\x06")
    if fault == "count":
        struct.pack_into("<HH", data, footer + 8, 1025, 1025)
    elif fault in {"extra", "comment", "name"}:
        struct.pack_into(
            "<H", data, directory + {"name": 28, "extra": 30, "comment": 32}[fault], 4097
        )
    elif fault == "offset":
        struct.pack_into("<L", data, footer + 16, 0xFFFFFFFF)
    elif fault == "disk":
        struct.pack_into("<H", data, footer + 4, 1)
    else:
        struct.pack_into("<HH", data, footer + 8, 65535, 65535)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("ZipFile allocated metadata before the bounded scan")

    monkeypatch.setattr(zipfile, "ZipFile", forbidden)
    with pytest.raises(ValueError):
        artifacts.binary_archive(bytes(data))
    source = tmp_path / "bundle.zip"
    source.write_bytes(data)
    with pytest.raises(ValueError):
        artifacts.stage_archive(source, tmp_path / "out", 1024)


def test_actual_directory_records_are_counted_when_eocd_lies() -> None:
    data = binary_bundle({"echor": elf_binary()})
    start = data.index(b"PK\x01\x02")
    end = data.index(b"PK\x05\x06")
    directory = data[start:end] * 1025
    footer = bytearray(data[end:])
    struct.pack_into("<L", footer, 12, len(directory))
    with pytest.raises(ValueError):
        artifacts.validate_zip_directory(io.BytesIO(data[:start] + directory + footer))


def test_valid_zip64_directory_and_position_are_preserved() -> None:
    data = binary_bundle({"echor": elf_binary()})
    end = data.index(b"PK\x05\x06")
    footer = bytearray(data[end:])
    _, _, _, count, _, size, offset, _ = struct.unpack("<4s4H2LH", footer)
    record = struct.pack("<4sQ2H2L4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, count, count, size, offset)
    locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, end, 1)
    struct.pack_into("<HHLL", footer, 8, 65535, 65535, 0xFFFFFFFF, 0xFFFFFFFF)
    bundle = data[:end] + record + locator + footer
    stream = io.BytesIO(bundle)
    stream.seek(3)
    artifacts.validate_zip_directory(stream)
    assert stream.tell() == 3
    assert artifacts.binary_archive(bundle) == {"echor": elf_binary()}
