"""Read declared Cargo members and targets without invoking Cargo.

Cargo validates the complete graph, including implicit path-dependency members.
Repository packages must be declared in the root workspace's member patterns.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import cast


def table(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("expected a TOML table")
    return cast(dict[str, object], value)


def tables(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ValueError("expected an array of TOML tables")
    return [table(item) for item in value]


def string(value: object, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location} must be a nonempty string")
    return value


def strings(value: object, location: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{location} must be an array of strings")
    return cast(list[str], value)


def load_toml(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as source:
            return cast(dict[str, object], tomllib.load(source))
    except (OSError, ValueError) as error:
        raise ValueError(f"Cannot read {path}: {error}") from error


@dataclass(frozen=True)
class Binary:
    name: str
    source: Path


@dataclass(frozen=True)
class Package:
    name: str
    directory: Path
    manifest: dict[str, object]
    binaries: tuple[Binary, ...]
    library: bool

    @property
    def is_cli(self) -> bool:
        # A package without either target is malformed, not an exempt library.
        return bool(self.binaries) or not self.library


def binary_targets(directory: Path, manifest: dict[str, object]) -> tuple[Binary, ...]:
    package = table(manifest["package"])
    name = string(package.get("name"), "package.name")
    candidates = [Binary(name, directory / "src/main.rs")]
    candidates.extend(Binary(path.stem, path) for path in sorted(directory.glob("src/bin/*.rs")))
    candidates.extend(
        Binary(path.parent.name, path) for path in sorted(directory.glob("src/bin/*/main.rs"))
    )
    explicit: list[Binary] = []
    for target in tables(manifest.get("bin", [])):
        target_name = string(target.get("name"), "bin.name")
        source = target.get("path")
        if source is None:
            source = next(
                (
                    str(item.source)
                    for item in candidates
                    if item.name == target_name and item.source.is_file()
                ),
                str(directory / f"src/bin/{target_name}.rs"),
            )
            explicit.append(Binary(target_name, Path(source).resolve()))
        else:
            explicit.append(Binary(target_name, (directory / string(source, "bin.path")).resolve()))
    if package.get("autobins") is False:
        return tuple(explicit)
    names = {target.name for target in explicit}
    paths = {target.source for target in explicit}
    return tuple(explicit) + tuple(
        Binary(item.name, item.source.resolve())
        for item in candidates
        if item.source.is_file() and item.name not in names and item.source.resolve() not in paths
    )


def discover(root: Path) -> list[Package]:
    root = root.resolve()
    manifest = load_toml(root / "Cargo.toml")
    workspace = table(manifest["workspace"])
    members = {
        directory.resolve()
        for pattern in strings(workspace.get("members", []), "workspace.members")
        for directory in root.glob(pattern)
    }
    excluded = {
        directory.resolve()
        for pattern in strings(workspace.get("exclude", []), "workspace.exclude")
        for directory in root.glob(pattern)
    }
    if "package" in manifest:
        members.add(root)
    result: list[Package] = []
    for directory in sorted(members - excluded):
        data = load_toml(directory / "Cargo.toml")
        package = table(data["package"])
        name = string(package.get("name"), f"{directory}/Cargo.toml package.name")
        library = "lib" in data or (
            package.get("autolib") is not False and (directory / "src/lib.rs").is_file()
        )
        result.append(Package(name, directory, data, binary_targets(directory, data), library))
    return result


def find_root(start: Path) -> Path:
    for directory in (start.resolve(), *start.resolve().parents):
        path = directory / "Cargo.toml"
        if path.is_file() and "workspace" in load_toml(path):
            return directory
    raise ValueError("Cannot find a Cargo workspace; run from the repository or use --root")
