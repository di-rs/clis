"""Build a private, content-addressed activation bundle without cloud credentials."""

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import cast

from .manifests import (
    Manifest,
    ReleaseError,
    environment,
    file_bytes,
    read_json,
    validate,
)


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def payload(manifest: Manifest, values: dict[str, str]) -> dict[str, object]:
    return {
        "descriptor": manifest.descriptor,
        "release": manifest.release,
        "environment": values,
        "files": {name: base64.b64encode(data).decode() for name, data in manifest.files.items()},
    }


def identity(bundle: dict[str, object]) -> str:
    return hashlib.sha256(canonical(bundle)).hexdigest()


def private_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(content)


def prepare(manifest: Manifest, values: dict[str, object], output: Path) -> str:
    resolved = environment(values, manifest)
    release_id = identity(payload(manifest, resolved))
    if output.exists():
        raise ReleaseError("prepared output already exists")
    output.mkdir(mode=0o700, parents=True)
    private_write(output / "descriptor.json", canonical(manifest.descriptor))
    private_write(output / "release.json", canonical(manifest.release))
    private_write(output / "environment.json", canonical(resolved))
    for name, content in manifest.files.items():
        private_write(output / "files" / name, content)
    private_write(output / "release-id", release_id.encode())
    return release_id


def load_prepared(root: Path) -> dict[str, object]:
    private = root / "environment.json"
    if (
        root.is_symlink()
        or private.is_symlink()
        or not private.is_file()
        or private.stat().st_mode & 0o077
    ):
        raise ReleaseError("prepared environment must remain a private regular file")
    descriptor = read_json(root / "descriptor.json")
    release = read_json(root / "release.json")
    wanted = {cast(str, descriptor.get("compose_file"))}
    wanted.update(cast(list[str], descriptor.get("files", [])))
    if descriptor.get("pre_activate"):
        wanted.add(cast(str, descriptor["pre_activate"]))
    files = {name: file_bytes(root / "files", name) for name in wanted}
    manifest = validate(descriptor, release, files)
    result = payload(manifest, environment(read_json(root / "environment.json"), manifest))
    try:
        if (root / "release-id").read_text() != identity(result):
            raise ReleaseError("prepared release content changed")
    except OSError as error:
        raise ReleaseError("missing prepared release identity") from error
    return result
