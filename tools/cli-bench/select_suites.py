#!/usr/bin/env python3.15
"""Select per-package benchmark suites for the complete pull-request diff."""

import argparse
import json
import os
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict, cast

ROOT = Path(__file__).resolve().parents[2]
GLOBAL_PATHS = {
    "Cargo.toml",
    "Cargo.lock",
    "rust-toolchain.toml",
    ".python-version",
    "tools/cli-bench/bench.py",
    "tools/cli-bench/select_suites.py",
    ".github/workflows/cli-bench.yml",
}


@dataclass(frozen=True)
class Suite:
    path: str
    package: str
    directory: str
    generate: bool


class Selection(TypedDict):
    suites: list[str]
    packages: list[str]
    binaries: list[str]
    runs: int
    warmup: int
    reason: str


def load_toml(path: Path) -> dict[str, object]:
    with path.open("rb") as source:
        return cast(dict[str, object], tomllib.load(source))


def discover_suites() -> list[Suite]:
    manifest = load_toml(ROOT / "Cargo.toml")
    workspace = cast(dict[str, list[str]], manifest["workspace"])
    excluded = {path for pattern in workspace.get("exclude", []) for path in ROOT.glob(pattern)}
    members = {path for pattern in workspace["members"] for path in ROOT.glob(pattern)}
    if "package" in manifest:
        members.add(ROOT)
    suites: list[Suite] = []
    for directory in sorted(members - excluded):
        package = cast(dict[str, str], load_toml(directory / "Cargo.toml")["package"])["name"]
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
                    relative,
                    package,
                    directory.relative_to(ROOT).as_posix(),
                    bool(suite.get("generate")),
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
    for path in paths:
        if path in GLOBAL_PATHS or path.startswith(".cargo/"):
            return suites
        parts = path.split("/")
        if len(parts) > 2 and parts[0] == "utils" and production_change(path, f"utils/{parts[1]}"):
            return suites
    generator_changed = any(production_change(path, "biggie") for path in paths)
    return [
        suite
        for suite in suites
        if (generator_changed and (suite.package == "biggie" or suite.generate))
        or any(path == suite.path or production_change(path, suite.directory) for path in paths)
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
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--event", type=Path, required=True)
    args = parser.parse_args()
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
