"""Read workspace members and targets from Cargo without building packages."""

import json
import subprocess
import tomllib
from pathlib import Path
from typing import cast

from pydantic import BaseModel, ConfigDict


class WorkspaceRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)


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


class Binary(WorkspaceRecord):
    name: str
    source: Path


class Package(WorkspaceRecord):
    name: str
    directory: Path
    manifest: dict[str, object]
    binaries: tuple[Binary, ...]
    library: bool
    enabled_tests: tuple[Path, ...] = ()

    @property
    def is_cli(self) -> bool:
        # A package without either target is malformed, not an exempt library.
        return bool(self.binaries) or not self.library


def discover(root: Path) -> list[Package]:
    root = root.resolve()
    command = [
        "cargo",
        "metadata",
        "--locked",
        "--no-deps",
        "--format-version",
        "1",
        "--manifest-path",
        str(root / "Cargo.toml"),
    ]
    try:
        output = subprocess.run(command, cwd=root, capture_output=True, text=True, check=True)
    except OSError as error:
        raise ValueError(f"Cannot run cargo metadata: {error}") from error
    except subprocess.CalledProcessError as error:
        raise ValueError(f"cargo metadata failed: {error.stderr.strip()}") from error
    metadata = table(json.loads(output.stdout))
    members = strings(metadata["workspace_members"], "workspace_members")
    result: list[Package] = []
    for package in tables(metadata["packages"]):
        if package["id"] not in members:
            continue
        manifest = Path(string(package["manifest_path"], "manifest_path")).resolve()
        targets = tables(package["targets"])
        result.append(
            Package(
                name=string(package["name"], "package.name"),
                directory=manifest.parent,
                manifest=load_toml(manifest),
                binaries=tuple(
                    Binary(
                        name=string(target["name"], "target.name"),
                        source=Path(string(target["src_path"], "target.src_path")).resolve(),
                    )
                    for target in targets
                    if "bin" in strings(target["kind"], "target.kind")
                ),
                library=any(
                    set(strings(target["kind"], "target.kind"))
                    & {"lib", "rlib", "dylib", "cdylib", "staticlib", "proc-macro"}
                    for target in targets
                ),
                enabled_tests=tuple(
                    Path(string(target["src_path"], "target.src_path")).resolve()
                    for target in targets
                    if "test" in strings(target["kind"], "target.kind") and target["test"]
                ),
            )
        )
    return sorted(result, key=lambda package: package.directory)


def find_root(start: Path) -> Path:
    for directory in (start.resolve(), *start.resolve().parents):
        path = directory / "Cargo.toml"
        if path.is_file() and "workspace" in load_toml(path):
            return directory
    raise ValueError("Cannot find a Cargo workspace; run from the repository or use --root")
