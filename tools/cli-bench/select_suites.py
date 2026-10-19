#!/usr/bin/env python3.15
"""Select per-package benchmark suites for the complete pull-request diff."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TypedDict, cast

from cli_check.affected import affected_packages, changed_paths
from cli_check.workspace import discover, load_toml
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
GLOBAL_PATHS = {
    "Cargo.toml",
    "rust-toolchain.toml",
    ".python-version",
    "tools/cli-bench/bench.py",
    "tools/cli-bench/select_suites.py",
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


def discover_suites() -> list[Suite]:
    suites: list[Suite] = []
    for member in discover(ROOT):
        directory, package = member.directory, member.name
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


def affected_suites(suites: list[Suite], base: str, head: str) -> list[Suite]:
    paths = changed_paths(ROOT, base, head)
    for path in paths:
        if path in GLOBAL_PATHS or path.startswith((".cargo/", "tools/cli-check/src/")):
            return suites
    affected = affected_packages(ROOT, base, head, paths)
    return [
        suite
        for suite in suites
        if ("biggie" in affected and suite.generate)
        or suite.package in affected
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
