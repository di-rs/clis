"""ZIPs are generic bytes; filesystem effects and ownership are the contract."""

import asyncio
import hashlib
import io
import shutil
import stat
import struct
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from boxie.domain.errors import BoxieError
from boxie.domain.models import Principal
from boxie.persistence.artifact_files import extract_archive
from boxie.persistence.artifacts import ArtifactLimits
from boxie_test_support.agent import bundle


class Archive:
    """Extraction harness only; SQL publication/ownership is covered in test_postgres."""

    def __init__(self, root: Path, limits: ArtifactLimits | None = None) -> None:
        self.root, self.limits = root, limits or ArtifactLimits()

    def path(self, principal: Principal, identifier: UUID) -> Path:
        return self.root / str(identifier) / "files"

    def exists(self, identifier: UUID) -> bool:
        return (self.root / str(identifier) / "files").exists()

    async def put(
        self, principal: Principal, identifier: UUID, digest: str, data: AsyncIterator[bytes]
    ) -> None:
        staged = self.root / str(identifier)
        staged.mkdir()
        try:
            with (staged / "archive.zip").open("wb") as output:
                async for chunk in data:
                    output.write(chunk)
            try:
                await extract_archive(staged, self.limits)
            except (
                zipfile.BadZipFile,
                NotImplementedError,
                RuntimeError,
                EOFError,
                FileExistsError,
                NotADirectoryError,
                IsADirectoryError,
                ValueError,
            ) as error:
                raise BoxieError(422, "invalid ZIP artifact") from error
        except BaseException:
            shutil.rmtree(staged)
            raise


async def chunks(data: bytes) -> AsyncIterator[bytes]:
    for offset in range(0, len(data), 7):
        yield data[offset : offset + 7]
        await asyncio.sleep(0)


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


@pytest.mark.parametrize(
    "kind",
    ["entries", "forged_count", "directory_bytes", "zip64_forged_count", "zip64_directory_bytes"],
)
def test_zip_metadata_is_rejected_before_eager_parser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for index in range(64 if kind.endswith("directory_bytes") else 20000):
            entry = zipfile.ZipInfo(f"file-{index}")
            if kind.endswith("directory_bytes"):
                entry.comment = b"x" * 65535
            archive.writestr(entry, b"")
    data = bytearray(out.getvalue())
    if kind.endswith("forged_count"):
        end = len(data) - 22
        struct.pack_into("<HH", data, end + 8, 1, 1)
    if kind.startswith("zip64_"):
        data = bytearray(zip64_directory(bytes(data)))

    def forbidden_parser(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("unbounded central directory reached eager ZipFile parser")

    monkeypatch.setattr("boxie.persistence.artifact_files.zipfile.ZipFile", forbidden_parser)

    async def exercise() -> None:
        async def upload_chunks() -> AsyncIterator[bytes]:
            yield bytes(data)

        limits = ArtifactLimits(files=1024 if kind.endswith("directory_bytes") else 1)
        store, identifier = Archive(tmp_path, limits), uuid4()
        with pytest.raises(BoxieError) as error:
            await store.put(
                Principal("one"), identifier, hashlib.sha256(data).hexdigest(), upload_chunks()
            )
        assert error.value.status == 413
        assert not store.exists(identifier)
        assert not list(tmp_path.glob(".upload-*"))

    asyncio.run(exercise())


def zip64_directory(original: bytes) -> bytes:
    end = len(original) - 22
    count = struct.unpack_from("<H", original, end + 10)[0]
    size, offset = struct.unpack_from("<II", original, end + 12)
    zip64 = struct.pack("<4sQHHIIQQQQ", b"PK\x06\x06", 44, 45, 45, 0, 0, count, count, size, offset)
    locator = struct.pack("<4sIQI", b"PK\x06\x07", 0, end, 1)
    footer = struct.pack("<4s4H2IH", b"PK\x05\x06", 0, 0, 65535, 65535, 0xFFFFFFFF, 0xFFFFFFFF, 0)
    return original[:end] + zip64 + locator + footer


def test_bounded_zip64_metadata_remains_compatible(tmp_path: Path) -> None:
    async def exercise() -> None:
        data = zip64_directory(bundle())
        store, identifier, principal = Archive(tmp_path), uuid4(), Principal("one")
        await store.put(principal, identifier, hashlib.sha256(data).hexdigest(), chunks(data))
        assert (
            store.path(principal, identifier) / "nested/hello.txt"
        ).read_bytes() == b"ordinary text\n"

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "kind",
    [
        "directory_count",
        "member_length",
        "comment_length",
        "split_disk",
        "zip64_offset",
        "zip64_record_size",
    ],
)
def test_inconsistent_metadata_is_rejected_before_eager_parser(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    data = bytearray(zip64_directory(bundle()) if kind.startswith("zip64_") else bundle())
    end = len(data) - 22
    if kind == "directory_count":
        struct.pack_into("<HH", data, end + 8, 0, 0)
    elif kind == "member_length":
        central = data.index(b"PK\x01\x02")
        struct.pack_into("<H", data, central + 28, 65535)
    elif kind == "comment_length":
        struct.pack_into("<H", data, end + 20, 1)
    elif kind == "split_disk":
        struct.pack_into("<H", data, end + 4, 1)
    elif kind == "zip64_offset":
        struct.pack_into("<Q", data, end - 12, len(data) + 1000)
    elif kind == "zip64_record_size":
        struct.pack_into("<Q", data, end - 72, 1000)

    def forbidden_parser(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("inconsistent metadata reached eager ZIP parser")

    monkeypatch.setattr("boxie.persistence.artifact_files.zipfile.ZipFile", forbidden_parser)

    async def exercise() -> None:
        with pytest.raises(BoxieError) as error:
            await Archive(tmp_path).put(
                Principal("one"), uuid4(), hashlib.sha256(data).hexdigest(), chunks(bytes(data))
            )
        assert error.value.status == 422
        assert not list(tmp_path.glob(".upload-*"))

    asyncio.run(exercise())


def test_valid_archive_comments_and_prefixed_zip_remain_compatible(tmp_path: Path) -> None:
    async def exercise() -> None:
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as archive:
            archive.comment = b"bounded archive comment"
            entry = zipfile.ZipInfo("text")
            entry.comment = b"member comment"
            archive.writestr(entry, b"generic text")
        data = b"small prepended data\n" + out.getvalue()
        principal, identifier = Principal("one"), uuid4()
        store = Archive(tmp_path)
        await store.put(principal, identifier, hashlib.sha256(data).hexdigest(), chunks(data))
        assert (store.path(principal, identifier) / "text").read_bytes() == b"generic text"

    asyncio.run(exercise())
