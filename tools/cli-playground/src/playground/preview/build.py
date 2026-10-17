"""Select CLI packages from an explicitly supplied source checkout."""

import re
import tomllib
from pathlib import Path

from playground.artifacts import CLI_PATTERN, MAX_CLIS

from .github import WORKFLOW


def discover(root: Path) -> dict[str, str]:
    manifest = tomllib.loads((root / "Cargo.toml").read_text())
    workspace = manifest["workspace"]
    excluded = {path for pattern in workspace.get("exclude", []) for path in root.glob(pattern)}
    members = {path for pattern in workspace["members"] for path in root.glob(pattern)}
    packages: dict[str, str] = {}
    for directory in sorted(members - excluded):
        data = tomllib.loads((directory / "Cargo.toml").read_text())
        if not (directory / "src/main.rs").is_file():
            continue
        name = data["package"]["name"]
        binaries = data.get("bin", [])
        if (
            not isinstance(name, str)
            or not re.fullmatch(CLI_PATTERN, name)
            or name != directory.name
            or name in packages
            or any((directory / "src/bin").glob("**/*.rs"))
            or len(binaries) > 1
            or binaries
            and (
                binaries[0].get("name") != name
                or binaries[0].get("path", "src/main.rs") != "src/main.rs"
            )
        ):
            raise ValueError("CLI package must define exactly its matching binary")
        packages[name] = directory.relative_to(root).as_posix()
    if not packages or len(packages) > MAX_CLIS:
        raise ValueError("unexpected CLI package count")
    return packages


def production(path: str, directory: str) -> bool:
    prefix = directory + "/"
    if not path.startswith(prefix):
        return False
    relative = path.removeprefix(prefix)
    return relative in {"Cargo.toml", "build.rs"} or relative.startswith("src/")


def select(packages: dict[str, str], paths: list[str]) -> list[str]:
    paths = [
        path
        for path in paths
        if not path.endswith(".md") and not {"tests", "docs"}.intersection(Path(path).parts)
    ]
    global_paths = {
        "Cargo.toml",
        "Cargo.lock",
        "rust-toolchain.toml",
        "tools/cli-playground/Build.Dockerfile",
        "tools/cli-playground/Build.Dockerfile.dockerignore",
        "tools/cli-playground/src/playground/preview/build.py",
        WORKFLOW,
    }
    if any(
        path in global_paths
        or path.startswith(".cargo/")
        or path.startswith("utils/")
        and "/src/" in path
        or path.startswith("utils/")
        and path.endswith(("/Cargo.toml", "/build.rs"))
        for path in paths
    ):
        return sorted(packages)
    return sorted(
        name
        for name, directory in packages.items()
        if any(production(path, directory) for path in paths)
    )
