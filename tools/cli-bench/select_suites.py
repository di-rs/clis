#!/usr/bin/env python3.15
"""Select per-package benchmark suites for the complete pull-request diff."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TypedDict

from cli_check.affected import changed_paths
from cli_check.benchmark import load_suite
from cli_check.workspace import discover
from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]


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
        if not member.is_cli:
            continue
        directory, package = member.directory, member.name
        for path in sorted(directory.glob("benches/*.toml")):
            suite = load_suite(path)
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


def affected_suites(suites: list[Suite], base: str, head: str) -> list[Suite]:
    paths = changed_paths(ROOT, base, head)
    return [
        suite
        for suite in suites
        if any(
            path == f"{suite.directory}/build.rs"
            or (path.startswith(f"{suite.directory}/src/") and path.endswith(".rs"))
            for path in paths
        )
    ]


def select(base: str, head: str, event: dict[str, object]) -> Selection:
    if event.get("action") == "edited":
        selected, reason = [], "PR edits do not trigger benchmarks"
    else:
        selected = affected_suites(discover_suites(), base, head)
        reason = "Changed CLI code smoke benchmarks" if selected else "No CLI code changes"
    binaries = sorted({suite.package for suite in selected})
    packages = set(binaries)
    if any(suite.generate for suite in selected):
        packages.add("biggie")
    return {
        "suites": [suite.path for suite in selected],
        "packages": sorted(packages),
        "binaries": binaries,
        "runs": 1,
        "warmup": 0,
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
