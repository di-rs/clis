#!/usr/bin/env python3.15
"""Select per-package benchmark suites for the complete pull-request diff."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TypedDict, cast

from cli_check.workspace import find_root, load_toml, string, table, tables
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path.cwd()
GLOBAL_PATHS = {
    "Cargo.toml",
    "Cargo.lock",
    "pyproject.toml",
    "uv.lock",
    "rust-toolchain.toml",
    ".python-version",
    ".github/workflows/cli-bench.yml",
}


class Suite(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    path: str = Field(min_length=1)
    package: str = Field(min_length=1)
    directory: str = Field(min_length=1)
    generate: bool


class Selection(TypedDict):
    suites: list[str]
    packages: list[str]
    binaries: list[str]
    runs: int
    warmup: int
    reason: str


def _cargo_packages(root: Path) -> list[dict[str, object]]:
    result = subprocess.run(
        ["cargo", "metadata", "--locked", "--format-version", "1", "--no-deps"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise ValueError("Cargo metadata failed: " + result.stderr.strip())
    return tables(table(json.loads(result.stdout))["packages"])


def _changed_packages(root: Path, paths: list[str], packages: list[dict[str, object]]) -> set[str]:
    directories = {
        str(Path(string(package["manifest_path"], "manifest_path")).parent.resolve()): string(
            package["name"], "package.name"
        )
        for package in packages
    }
    changed: set[str] = set()
    for path in paths:
        owners = [
            (directory, name)
            for directory, name in directories.items()
            if (root / path).is_relative_to(directory)
        ]
        if owners:
            directory, name = max(owners, key=lambda item: len(item[0]))
            relative = (root / path).relative_to(directory).as_posix()
            if relative in {"Cargo.toml", "build.rs"} or relative.startswith("src/"):
                changed.add(name)
        elif ("/src/" in path and path.endswith(".rs")) or path.endswith(
            ("/Cargo.toml", "/build.rs")
        ):
            changed.update(directories.values())
    while True:
        before = set(changed)
        for package in packages:
            for dependency in tables(package["dependencies"]):
                location = dependency.get("path")
                if (
                    isinstance(location, str)
                    and directories.get(str(Path(location).resolve())) in changed
                ):
                    changed.add(string(package["name"], "package.name"))
        if before == changed:
            return changed


def _tool_change(root: Path, paths: list[str]) -> bool:
    for name in ("cli-bench", "cli-check"):
        if not (root / "pyproject.toml").is_file():
            continue
        result = subprocess.run(
            ["uv", "workspace", "dir", "--package", name],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise ValueError("Workspace lookup failed: " + result.stderr.strip())
        directory = Path(result.stdout.strip()).resolve().relative_to(root).as_posix()
        if any(
            path.startswith(directory + "/src/") or path == directory + "/pyproject.toml"
            for path in paths
        ):
            return True
    return False


def discover_suites() -> list[Suite]:
    suites: list[Suite] = []
    for member in _cargo_packages(ROOT):
        directory = Path(string(member["manifest_path"], "manifest_path")).parent
        package = string(member["name"], "package.name")
        for path in sorted(directory.glob("benches/*.toml")):
            suite = load_toml(path)
            relative = path.relative_to(ROOT).as_posix()
            if suite.get("binary") != package:
                raise ValueError(
                    f"{relative}: suite binary {suite.get('binary')!r} must match "
                    f"owning package {package!r}"
                )
            suites.append(
                Suite(
                    path=relative,
                    package=package,
                    directory=directory.relative_to(ROOT).as_posix(),
                    generate=bool(suite.get("generate")),
                )
            )
    return sorted(suites, key=lambda suite: suite.path)


def parse_request(body: object) -> frozenset[str] | None:
    if body is None:
        return None
    if not isinstance(body, str):
        raise ValueError("pull_request.body must be text or null")
    fields = [
        line.strip().removeprefix("Benchmark-CLIs:").strip()
        for line in body.splitlines()
        if line.strip().startswith("Benchmark-CLIs:")
    ]
    if len(fields) > 1:
        raise ValueError("duplicate Benchmark-CLIs fields; use one standalone field")
    if not fields or fields[0] in {"", "auto"}:
        return None
    names = frozenset(name.strip() for name in fields[0].split(","))
    if "" in names:
        raise ValueError("Benchmark-CLIs must contain nonempty comma-separated CLI names")
    return names


def production_change(path: str, directory: str) -> bool:
    prefix = "" if directory == "." else directory + "/"
    if not path.startswith(prefix):
        return False
    relative = path.removeprefix(prefix)
    return relative in {"Cargo.toml", "build.rs"} or relative.startswith("src/")


def affected_suites(suites: list[Suite], base: str, head: str) -> list[Suite]:
    diff = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "-z", f"{base}...{head}", "--"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    paths = [os.fsdecode(path) for path in diff.stdout.split(b"\0") if path]
    if any(path in GLOBAL_PATHS or path.startswith(".cargo/") for path in paths) or _tool_change(
        ROOT, paths
    ):
        return suites
    changed = _changed_packages(ROOT, paths, _cargo_packages(ROOT))
    return [
        suite
        for suite in suites
        if suite.package in changed
        or ("biggie" in changed and suite.generate)
        or suite.path in paths
    ]


def select(base: str, head: str, event: dict[str, object]) -> Selection:
    suites = discover_suites()
    pull_request = cast(dict[str, object], event.get("pull_request", {}))
    request = parse_request(pull_request.get("body"))
    available = {suite.package for suite in suites}
    if request is not None and request != {"all"}:
        unknown = request - available
        if unknown:
            raise ValueError(
                f"unknown Benchmark-CLIs: {', '.join(sorted(unknown))}; "
                f"available CLIs: {', '.join(sorted(available))}"
            )
    unchanged = False
    if event.get("action") == "edited":
        changes = cast(dict[str, object], event.get("changes", {}))
        body_change = cast(dict[str, object], changes.get("body", {}))
        if "from" in body_change:
            try:
                unchanged = request == parse_request(body_change["from"])
            except ValueError:
                pass  # Correcting an invalid previous request must still trigger a run.
    if unchanged:
        selected, reason = [], "Benchmark request unchanged"
    elif request is None:
        selected = affected_suites(suites, base, head)
        reason = "Affected CLI smoke benchmarks" if selected else "No affected CLI benchmarks"
    else:
        selected = [suite for suite in suites if request == {"all"} or suite.package in request]
        reason = "Explicit Benchmark-CLIs request"
    binaries = sorted({suite.package for suite in selected})
    packages = set(binaries)
    if any(suite.generate for suite in selected):
        packages.add("biggie")
    full = request is not None and not unchanged
    return {
        "suites": [suite.path for suite in selected],
        "packages": sorted(packages),
        "binaries": binaries,
        "runs": 20 if full else 1,
        "warmup": 3 if full else 0,
        "reason": reason,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--event", type=Path, required=True)
    args = parser.parse_args()
    global ROOT
    ROOT = args.root.resolve() if args.root else find_root(Path.cwd())
    try:
        event: dict[str, object] = json.loads(args.event.read_text())
        selection = select(args.base, args.head, event)
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with Path(summary_path).open("a") as summary:
                summary.write(
                    f"### CLI benchmark selection\n\n{selection['reason']}.\n\n"
                    f"CLIs: {', '.join(selection['binaries']) or 'none'}. "
                    f"Runs: {selection['runs']}; warmup: {selection['warmup']}.\n\n"
                )
        print(json.dumps(selection))
    except subprocess.CalledProcessError as error:
        sys.exit(f"select_suites: git diff failed: {os.fsdecode(error.stderr).strip()}")
    except (OSError, ValueError, KeyError, TypeError) as error:
        sys.exit(f"select_suites: {error}")


if __name__ == "__main__":
    main()
