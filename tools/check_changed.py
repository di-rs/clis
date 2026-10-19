"""Scope local prek commands; CI and shared configuration retain full checks."""

import json
import os
import shlex
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import TypedDict, cast

FULL = {
    "cargo-fmt": "cargo fmt --all",
    "cargo-clippy": "cargo clippy --locked --workspace --all-targets --all-features -- -D warnings -F unsafe_code",
    "cargo-nexttest": "cargo nextest run --locked --workspace --hide-progress-bar --failure-output final",
    "ruff": "uv run --locked ruff check .",
    "ruff-format": "uv run --locked ruff format --check .",
    "mypy": "uv run --locked mypy",
    "python-tests": "uv run --locked pytest",
    "actionlint": "actionlint -color",
    "zizmor": "zizmor --offline --no-progress .github/workflows",
}
SHARED = {"prek.toml", "tools/check_changed.py", "pyproject.toml", "uv.lock", ".python-version"}
RUST_CONFIG = {
    "Cargo.toml",
    "Cargo.lock",
    "rust-toolchain.toml",
    "rustfmt.toml",
    ".rustfmt.toml",
    "clippy.toml",
    ".clippy.toml",
}


class Dependency(TypedDict):
    path: str


class CargoPackage(TypedDict):
    name: str
    manifest_path: str
    dependencies: list[Dependency]


def rust_packages(paths: list[str], root: Path, dependents: bool) -> list[str] | None:
    result = subprocess.run(
        ["cargo", "metadata", "--locked", "--no-deps", "--format-version", "1"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    packages = cast(list[CargoPackage], json.loads(result.stdout)["packages"])
    directories = {Path(p["manifest_path"]).parent: p["name"] for p in packages}
    selected: set[str] = set()
    for path in paths:
        owners = [
            name
            for directory, name in directories.items()
            if (root / path).is_relative_to(directory)
        ]
        if not owners:
            return None
        selected.update(owners)
    if dependents:
        while True:
            expanded = selected | {
                p["name"]
                for p in packages
                if any(
                    directories.get(Path(d["path"])) in selected
                    for d in p["dependencies"]
                    if "path" in d
                )
            }
            if expanded == selected:
                break
            selected = expanded
    return sorted(selected)


def python_areas(paths: list[str], root: Path) -> list[str] | None:
    with (root / "pyproject.toml").open("rb") as source:
        testpaths = tomllib.load(source)["tool"]["pytest"]["testpaths"]
    if any(path.startswith("packages/boxie/tests/support/") for path in paths):
        return None
    areas = {str(Path(path).parent) for path in testpaths}
    selected: set[str] = set()
    for path in paths:
        owners = {area for area in areas if Path(path).is_relative_to(area)}
        if not owners:
            return None
        selected.update(owners)
    # shortcut: Python consumer groups are explicit; expand when adding cross-area imports.
    services = {
        area for area in areas if area.startswith(("packages/", "infra/", "tools/cli-playground"))
    }
    if selected & services:
        selected.update(services)
    if "tools/cli-check" in selected:
        selected.add("tools/cli-bench")
    return sorted(selected)


def command(hook: str, paths: list[str], root: Path) -> list[str]:
    full = shlex.split(FULL[hook])
    if os.environ.get("CI", "").lower() in {"true", "1"} or SHARED.intersection(paths):
        return full
    if hook.startswith("cargo-"):
        if any(
            path.startswith((".cargo/", ".config/")) or Path(path).name in RUST_CONFIG
            for path in paths
        ):
            return full
        packages = rust_packages(paths, root, dependents=hook != "cargo-fmt")
        if packages is None:
            return full
        selection = [arg for package in packages for arg in ("-p", package)]
        if hook == "cargo-fmt":
            return ["cargo", "fmt", *selection]
        index = full.index("--workspace")
        return full[:index] + selection + full[index + 1 :]
    if hook in {"ruff", "ruff-format"}:
        if any(Path(path).name in {"pyproject.toml", "ruff.toml", ".ruff.toml"} for path in paths):
            return full
        sources = [
            path
            for path in paths
            if Path(path).suffix in {".py", ".pyi"} and (root / path).is_file()
        ]
        return [*full[:-1], "--force-exclude", "--", *sources] if sources else []
    if hook in {"mypy", "python-tests"}:
        areas = python_areas(paths, root)
        if areas is None:
            return full
        return full + [f"{area}/tests" if hook == "python-tests" else area for area in areas]
    if any(
        Path(path).name in {"zizmor.yml", "zizmor.yaml", "actionlint.yml", "actionlint.yaml"}
        for path in paths
    ):
        return full
    existing = [path for path in paths if (root / path).is_file()]
    if not existing:
        return []
    return (full[:-1] if hook == "zizmor" else full) + existing


def main() -> int:
    try:
        args = command(sys.argv[1], sys.argv[2:], Path.cwd())
        if not args:
            return 0
        print(shlex.join(args), flush=True)
        return subprocess.run(args, check=False).returncode
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f"Cannot select checks: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
