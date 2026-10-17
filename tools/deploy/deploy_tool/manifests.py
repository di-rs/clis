"""Validate source-owned contracts before an activation can reach a host."""

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import cast

import yaml


class ReleaseError(Exception):
    """A safe diagnostic: never include input values or subprocess output."""


def object_map(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise ReleaseError(f"invalid {label} object")
    return cast(dict[str, object], value)


def json_bytes(content: bytes, *, limit: int = 65536) -> dict[str, object]:
    if len(content) > limit:
        raise ReleaseError("JSON metadata exceeds 64 KiB")

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ReleaseError("duplicate JSON key")
            result[key] = value
        return result

    try:
        return object_map(json.loads(content, object_pairs_hook=unique), "JSON")
    except (ValueError, UnicodeError) as error:
        raise ReleaseError("cannot read JSON input") from error


def read_json(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as stream:
            return json_bytes(stream.read(65537))
    except OSError as error:
        raise ReleaseError("cannot read JSON input") from error


@dataclass(frozen=True)
class ReleaseIdentity:
    component: str
    revision: str
    images: dict[str, str]
    configuration_hashes: dict[str, str]
    compatibility: dict[str, str]
    settings: dict[str, str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def string(value: object, label: str, pattern: str | None = None) -> str:
    if not isinstance(value, str) or not value or "\0" in value:
        raise ReleaseError(f"invalid {label}")
    if pattern is not None and re.fullmatch(pattern, value) is None:
        raise ReleaseError(f"invalid {label}")
    return value


def names(value: object, label: str, pattern: str = r"[a-zA-Z0-9][a-zA-Z0-9_.-]*") -> list[str]:
    if not isinstance(value, list):
        raise ReleaseError(f"invalid {label}")
    result = [string(item, label, pattern) for item in value]
    if len(result) != len(set(result)):
        raise ReleaseError(f"duplicate {label}")
    return result


def relative_file(value: object) -> str:
    name = string(value, "relative file")
    if name in ("bundle.json", "environment.json"):
        raise ReleaseError("component file uses reserved remote metadata path")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or str(path) != name or name == ".":
        raise ReleaseError("release files must be normalized relative paths")
    return name


def file_bytes(root: Path, name: str) -> bytes:
    path = root / relative_file(name)
    if not path.is_file() or path.is_symlink() or root.resolve() not in path.resolve().parents:
        raise ReleaseError("release file missing or outside source directory")
    if any(
        (root / parent).is_symlink()
        for parent in path.relative_to(root).parents
        if parent != Path(".")
    ):
        raise ReleaseError("release file cannot use symbolic links")
    content = path.read_bytes()
    if len(content) > 5_000_000:
        raise ReleaseError("release file exceeds size limit")
    return content


@dataclass(frozen=True)
class Manifest:
    descriptor: dict[str, object]
    release: dict[str, object]
    files: dict[str, bytes]

    @property
    def component(self) -> str:
        return cast(str, self.descriptor["component"])


def validate(
    descriptor: dict[str, object], release: dict[str, object], files: dict[str, bytes]
) -> Manifest:
    component = string(descriptor.get("component"), "component", r"[a-z][a-z0-9-]{0,47}")
    if release.get("component") != component:
        raise ReleaseError("release component does not match descriptor")
    string(release.get("revision"), "immutable revision", r"[a-f0-9]{40}|[a-f0-9]{64}")
    compatibility = object_map(release.get("compatibility"), "compatibility")
    string(compatibility.get("schema"), "schema compatibility")
    for key, value in compatibility.items():
        string(key, "compatibility key", r"[a-zA-Z0-9_.-]+")
        string(value, "compatibility token")
    compose_name = relative_file(descriptor.get("compose_file"))
    services = names(descriptor.get("services"), "services")
    if not services:
        raise ReleaseError("component needs services")
    source_files = [
        relative_file(item) for item in names(descriptor.get("files", []), "files", r".+")
    ]
    if descriptor.get("pre_activate") is not None:
        source_files.append(relative_file(descriptor["pre_activate"]))
    if set(files) != {compose_name, *source_files}:
        raise ReleaseError("release files do not match descriptor")
    if len(files) > 64 or sum(map(len, files.values())) > 10_000_000:
        raise ReleaseError("release exceeds size limit")
    images = object_map(release.get("images"), "images")
    variables = object_map(descriptor.get("image_variables"), "image variables")
    if images.keys() != variables.keys():
        raise ReleaseError("release image variables do not match descriptor")
    owned: dict[str, str] = {}
    for key, members in variables.items():
        string(key, "image variable", r"[A-Z][A-Z0-9_]*")
        string(
            images[key],
            "immutable image",
            r"[a-z0-9][a-z0-9.:-]*/[a-z0-9][a-z0-9./_-]*@sha256:[a-f0-9]{64}",
        )
        for service in names(members, "image services"):
            if service not in services or service in owned:
                raise ReleaseError("invalid image service ownership")
            owned[service] = key
    if set(owned) != set(services):
        raise ReleaseError("every service needs an immutable image")
    required = names(
        descriptor.get("required_environment", []), "environment keys", r"[A-Z][A-Z0-9_]*"
    )
    if set(required) & variables.keys():
        raise ReleaseError("image variables cannot be environment requirements")
    private = names(descriptor.get("secret_environment", []), "secret names", r"[A-Z][A-Z0-9_]*")
    if set(private) & (set(required) | variables.keys()):
        raise ReleaseError("secret names must be disjoint from settings and images")
    settings = object_map(release.get("settings", {}), "settings")
    if set(private) & settings.keys():
        raise ReleaseError("private values cannot enter public settings")
    health = object_map(descriptor.get("healthchecks", {}), "healthchecks")
    if set(health) - set(services):
        raise ReleaseError("healthcheck outside component")
    for value in health.values():
        check = object_map(value, "healthcheck")
        if check.get("kind") not in ("docker", "exec"):
            raise ReleaseError("invalid healthcheck kind")
        if check["kind"] == "exec" and not names(check.get("command"), "health command", r".+"):
            raise ReleaseError("empty health command")
    try:
        compose = object_map(yaml.safe_load(files[compose_name]), "Compose")
    except (yaml.YAMLError, UnicodeError) as error:
        raise ReleaseError("invalid Compose document") from error
    if "include" in compose:
        raise ReleaseError("Compose includes are outside release ownership")
    workloads = object_map(compose.get("services"), "Compose services")
    if workloads.keys() != set(services):
        raise ReleaseError("Compose services do not match component")
    for service, value in workloads.items():
        workload = object_map(value, "Compose service")
        if "extends" in workload:
            raise ReleaseError("Compose extends is outside release ownership")
        image = string(workload.get("image"), "Compose image")
        if re.fullmatch(r"\$\{" + owned[service] + r"(?::[-?][^}]+)?\}", image) is None:
            raise ReleaseError("Compose image must use its declared immutable image variable")
    for kind in ("networks", "volumes"):
        for value in object_map(compose.get(kind, {}), f"Compose {kind}").values():
            if object_map(value, f"Compose {kind}").get("external") is not True:
                raise ReleaseError("shared networks and volumes require external ownership")
    overlays = object_map(descriptor.get("optional_compose_files", {}), "optional Compose files")
    for setting, filename in overlays.items():
        string(setting, "overlay selector", r"[A-Z][A-Z0-9_]*")
        if (
            setting in private
            or setting in variables
            or settings.get(setting, "false") not in ("true", "false")
        ):
            raise ReleaseError("overlay selector must be a public boolean string")
        name = relative_file(filename)
        if name not in source_files:
            raise ReleaseError("overlay must be a committed component file")
        overlay = object_map(yaml.safe_load(files[name]), "Compose overlay")
        if set(overlay) - {"services", "networks", "volumes"}:
            raise ReleaseError("unsupported monitoring overlay fields")
        for kind in ("networks", "volumes"):
            for value in object_map(overlay.get(kind, {}), "overlay resources").values():
                if object_map(value, "overlay resource").get("external") is not True:
                    raise ReleaseError("overlay resources require external ownership")
        for service, value in object_map(overlay.get("services", {}), "overlay services").items():
            if service not in services or set(object_map(value, "overlay service")) - {
                "networks",
                "environment",
                "labels",
            }:
                raise ReleaseError("monitoring overlay cannot replace application ownership")
    return Manifest(descriptor, release, files)


def source_manifest(
    repository: Path,
    descriptor_name: str,
    revision: str,
    images: dict[str, object],
    settings: dict[str, object],
) -> Manifest:
    from .source import committed_files

    relative_file(descriptor_name)
    selected = committed_files(repository, revision, (descriptor_name,))
    descriptor = json_bytes(selected[descriptor_name])
    root = str(PurePosixPath(descriptor_name).parent)
    wanted = {relative_file(descriptor.get("compose_file"))}
    wanted.update(
        relative_file(item) for item in names(descriptor.get("files", []), "files", r".+")
    )
    if descriptor.get("pre_activate"):
        wanted.add(relative_file(descriptor["pre_activate"]))
    paths = tuple(str(PurePosixPath(root) / name) for name in sorted(wanted))
    content = committed_files(repository, revision, paths)
    files = {name: content[str(PurePosixPath(root) / name)] for name in wanted}
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in (selected | content).items()}
    identity = ReleaseIdentity(
        string(descriptor.get("component"), "component"),
        revision,
        {k: string(v, "image") for k, v in images.items()},
        hashes,
        {
            k: string(v, "compatibility")
            for k, v in object_map(descriptor.get("compatibility"), "compatibility").items()
        },
        {k: string(v, "setting") for k, v in settings.items()},
    )
    manifest = validate(descriptor, identity.to_dict(), files)
    environment({}, manifest, require_private=False)
    return manifest


def load(descriptor_path: Path, release_path: Path, repository: Path) -> Manifest:
    release = read_json(release_path)
    manifest = source_manifest(
        repository,
        descriptor_path.as_posix(),
        string(release.get("revision"), "revision"),
        object_map(release.get("images"), "images"),
        object_map(release.get("settings"), "settings"),
    )
    if manifest.release != release:
        raise ReleaseError("release manifest does not match committed configuration")
    return manifest


def environment(
    value: dict[str, object], manifest: Manifest, *, require_private: bool = True
) -> dict[str, str]:
    private = set(names(manifest.descriptor.get("secret_environment", []), "secret names"))
    if set(value) - private:
        raise ReleaseError("unknown private environment name")
    if require_private and any(not value.get(key) for key in private):
        raise ReleaseError("missing required component secret")
    resolved: dict[str, str] = {}
    combined = object_map(manifest.release.get("settings", {}), "settings") | value
    for key, item in combined.items():
        string(key, "environment key", r"[A-Z][A-Z0-9_]*")
        if key.startswith(("CLIS_", "COMPOSE_", "DOCKER_", "LD_", "PYTHON")) or key in (
            "PATH",
            "HOME",
            "ENV",
            "BASH_ENV",
            "SHELLOPTS",
        ):
            raise ReleaseError("reserved deployment environment key")
        if not isinstance(item, str) or "\0" in item:
            raise ReleaseError("invalid environment value")
        resolved[key] = item
    for key in cast(list[str], manifest.descriptor.get("required_environment", [])):
        if not resolved.get(key):
            raise ReleaseError("missing required component environment")
    images = object_map(manifest.release["images"], "images")
    if resolved.keys() & images.keys():
        raise ReleaseError("environment cannot replace immutable images")
    return resolved
