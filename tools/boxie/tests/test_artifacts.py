"""ZIPs are generic bytes; filesystem effects and ownership are the contract."""

import asyncio
import hashlib
import io
import stat
import struct
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest
from boxie.domain.errors import BoxieError
from boxie.domain.models import Principal
from boxie.persistence.artifacts import ArtifactLimits
from boxie_test_support.archive import Archive, chunks
from boxie_test_support.orchestrator import bundle


@pytest.mark.parametrize(
    "name", [".", "/escape", "../escape", "a/../../escape", "a\\escape", "C:/escape"]
)
def test_unsafe_archive_paths_are_rejected(tmp_path: Path, name: str) -> None:
    async def exercise() -> None:
        data = bundle({name: b"unsafe"})
        with pytest.raises(BoxieError):
            await Archive(tmp_path).put(
                Principal("one"), uuid4(), hashlib.sha256(data).hexdigest(), chunks(data)
            )
        assert not list(tmp_path.glob("*/files"))
        assert not list(tmp_path.glob(".upload-*"))

    asyncio.run(exercise())


@pytest.mark.parametrize("kind", ["duplicate", "symlink", "device", "encrypted", "compression"])
def test_unsafe_zip_members(tmp_path: Path, kind: str) -> None:
    async def exercise() -> None:
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            entry = zipfile.ZipInfo("file")
            entry.create_system = 3
            entry.external_attr = (
                (
                    stat.S_IFLNK
                    if kind == "symlink"
                    else stat.S_IFCHR
                    if kind == "device"
                    else stat.S_IFREG
                )
                | 0o644
            ) << 16
            archive.writestr(entry, b"text")
            if kind == "duplicate":
                archive.writestr("./file", b"again")
        data = bytearray(out.getvalue())
        central = data.index(b"PK\x01\x02")
        if kind == "encrypted":
            struct.pack_into("<H", data, 6, 1)
            struct.pack_into("<H", data, central + 8, 1)
        if kind == "compression":
            struct.pack_into("<H", data, 8, 99)
            struct.pack_into("<H", data, central + 10, 99)
        with pytest.raises(BoxieError):
            await Archive(tmp_path).put(
                Principal("one"), uuid4(), hashlib.sha256(data).hexdigest(), chunks(bytes(data))
            )
        assert not list(tmp_path.glob(".upload-*"))

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "limits",
    [
        ArtifactLimits(expanded_bytes=10),
        ArtifactLimits(member_bytes=10),
        ArtifactLimits(files=1),
    ],
)
def test_limits_count_actual_bytes_and_members(tmp_path: Path, limits: ArtifactLimits) -> None:
    async def exercise() -> None:
        data = bundle({"a": b"a" * 11, "b": b"b"})
        with pytest.raises(BoxieError):
            await Archive(tmp_path, limits).put(
                Principal("one"), uuid4(), hashlib.sha256(data).hexdigest(), chunks(data)
            )
        assert not list(tmp_path.glob(".upload-*"))

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "files", [{"a": b"file", "a/b": b"child"}, {"a/b": b"child", "a": b"file"}]
)
def test_file_directory_conflicts_are_validation_errors(
    tmp_path: Path, files: dict[str, bytes]
) -> None:
    async def exercise() -> None:
        data = bundle(files)
        with pytest.raises(BoxieError) as error:
            await Archive(tmp_path).put(
                Principal("one"), uuid4(), hashlib.sha256(data).hexdigest(), chunks(data)
            )
        assert error.value.status == 422
        assert not list(tmp_path.glob(".upload-*"))

    asyncio.run(exercise())


def test_executable_bits_are_safe_and_bad_size_headers_cannot_publish(tmp_path: Path) -> None:
    async def exercise() -> None:
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
            entry = zipfile.ZipInfo("script")
            entry.create_system = 3
            entry.external_attr = (stat.S_IFREG | 0o6777) << 16
            archive.writestr(entry, b"#!/bin/sh\nprintf hello\n")
        data = out.getvalue()
        store, identifier, principal = Archive(tmp_path), uuid4(), Principal("one")
        await store.put(principal, identifier, hashlib.sha256(data).hexdigest(), chunks(data))
        assert (store.path(principal, identifier) / "script").stat().st_mode & 0o7777 == 0o555
        forged = bytearray(data)
        central = forged.index(b"PK\x01\x02")
        struct.pack_into("<I", forged, central + 24, 1)
        forged_id = uuid4()
        with pytest.raises(BoxieError):
            await store.put(
                principal, forged_id, hashlib.sha256(forged).hexdigest(), chunks(bytes(forged))
            )
        assert not store.exists(forged_id)

    asyncio.run(exercise())
