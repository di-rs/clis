#!/usr/bin/env python3.15
"""Build affected Cargo binaries at the merge base and head; record executable bytes."""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

from cli_check.affected import git, lock_closures, production_change
from cli_check.workspace import Package, discover, string, strings, table
from size_data import BinarySize, Build, SizeReport

ROOT = Path(__file__).resolve().parents[2]


def selected_packages(before: Path, after: Path, paths: list[str]) -> set[str]:
    members = [*discover(before), *discover(after)]
    binaries = {member.name for member in members if member.binaries}
    global_files = {
        "Cargo.toml",
        "rust-toolchain",
        "rust-toolchain.toml",
        "tools/cli-bench/collect_sizes.py",
        "tools/cli-bench/size_data.py",
        "tools/cli-bench/size_report.py",
        "tools/cli-bench/report.py",
        "tools/cli-check/src/cli_check/affected.py",
        "tools/cli-check/src/cli_check/workspace.py",
        ".github/workflows/cli-size.yml",
        ".github/workflows/cli-size-report.yml",
    }
    if any(path in global_files or path.startswith(".cargo/") for path in paths):
        return binaries
    changed: set[str] = set()
    for root in (before, after):
        for member in discover(root):
            directory = member.directory.relative_to(root.resolve()).as_posix()
            if any(production_change(path, directory) for path in paths):
                changed.add(member.name)
    if not changed and "Cargo.lock" not in paths:
        return set()
    old = lock_closures((before / "Cargo.lock").read_text())
    new = lock_closures((after / "Cargo.lock").read_text())
    selected = changed & binaries
    for name in binaries:
        if old.get(name) != new.get(name) or any(
            not source and dependency in changed
            for closure in (old.get(name, {}), new.get(name, {}))
            for dependency, _, source in closure
        ):
            selected.add(name)
    return selected


def build_sizes(
    root: Path, members: list[Package], selected: set[str], target: Path
) -> dict[tuple[str, str], Build]:
    results: dict[tuple[str, str], Build] = {}
    for member in members:
        if member.name not in selected or not member.binaries:
            continue
        expected = {binary.name for binary in member.binaries}
        # One package per invocation keeps feature unification independent of the selected set.
        command = [
            "cargo",
            "build",
            "--locked",
            "--release",
            "--bins",
            "-p",
            member.name,
            "--target-dir",
            str(target),
            "--message-format=json-render-diagnostics",
        ]
        result = subprocess.run(command, cwd=root, text=True, stdout=subprocess.PIPE, check=False)
        found: dict[str, int] = {}
        for line in result.stdout.splitlines():
            message = table(json.loads(line))
            if message.get("reason") != "compiler-artifact" or not message.get("executable"):
                continue
            artifact = table(message["target"])
            name = string(artifact.get("name"), "target name")
            if name not in expected or "bin" not in strings(
                artifact.get("kind", []), "target kind"
            ):
                continue
            executable = Path(string(message["executable"], "executable")).resolve()
            if not executable.is_relative_to(target.resolve()):
                raise ValueError("executable outside build directory")
            found[name] = executable.stat().st_size
        for name in expected:
            results[member.name, name] = (
                Build(status="complete", bytes=found[name])
                if result.returncode == 0 and name in found
                else Build(status="failed")
            )
    return results


def collect(root: Path, base: str, head: str, attempt: int, output: Path) -> SizeReport:
    base_sha = git(root, "merge-base", base, head).strip()
    head_sha = git(root, "rev-parse", head).strip()
    # Validate revisions before passing them to checkout commands.
    if not all(re.fullmatch(r"[0-9a-f]{40}", sha) for sha in (base_sha, head_sha)):
        raise ValueError("invalid revision")
    paths = git(root, "diff", "--name-only", "--no-renames", "-z", base_sha, head_sha).split("\0")[
        :-1
    ]
    toolchain = string(
        table(tomllib.loads(git(root, "show", f"{head_sha}:rust-toolchain.toml"))["toolchain"])[
            "channel"
        ],
        "toolchain channel",
    )
    # Both snapshots use the PR's compiler, even if the base pins another compiler.
    os.environ["RUSTUP_TOOLCHAIN"] = toolchain
    compiler = subprocess.run(["rustc", "-vV"], check=True, capture_output=True, text=True).stdout
    host = next(
        line.removeprefix("host: ") for line in compiler.splitlines() if line.startswith("host: ")
    )
    os.environ["CARGO_BUILD_TARGET"] = host
    with tempfile.TemporaryDirectory(prefix="cli-sizes-") as directory:
        work = Path(directory)
        before, after = work / "base", work / "head"
        added: list[Path] = []
        try:
            for path, sha in ((before, base_sha), (after, head_sha)):
                git(root, "worktree", "add", "--detach", str(path), sha)
                added.append(path)
            selected = selected_packages(before, after, paths)
            old_members, new_members = discover(before), discover(after)
            keys = sorted(
                {
                    (member.name, binary.name)
                    for member in [*old_members, *new_members]
                    if member.name in selected
                    for binary in member.binaries
                }
            )
            old_keys = {
                (member.name, binary.name) for member in old_members for binary in member.binaries
            }
            new_keys = {
                (member.name, binary.name) for member in new_members for binary in member.binaries
            }
            report = SizeReport(
                base_sha=base_sha,
                head_sha=head_sha,
                run_attempt=attempt,
                toolchain=toolchain,
                target=host,
                rows=[
                    BinarySize(
                        package=p,
                        binary=b,
                        base=Build(status="failed" if (p, b) in old_keys else "absent"),
                        head=Build(status="failed" if (p, b) in new_keys else "absent"),
                    )
                    for p, b in keys
                ],
            )
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(report.model_dump_json(indent=2) + "\n")
            for label, snapshot, members in (
                ("base", before, old_members),
                ("head", after, new_members),
            ):
                results = build_sizes(snapshot, members, selected, work / f"target-{label}")
                for row in report.rows:
                    if (build := results.get((row.package, row.binary))) is not None:
                        setattr(row, label, build)
                output.write_text(report.model_dump_json(indent=2) + "\n")
            return report
        finally:
            for path in reversed(added):
                git(root, "worktree", "remove", "--force", str(path))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--attempt", type=int, default=1)
    args = parser.parse_args()
    try:
        report = collect(
            args.root.resolve(), args.base, args.head, args.attempt, args.output.resolve()
        )
        if any(build.status == "failed" for row in report.rows for build in (row.base, row.head)):
            sys.exit("Some binary builds failed; see the size report and build logs.")
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        sys.exit(f"Cannot collect sizes: {error}")


if __name__ == "__main__":
    main()
