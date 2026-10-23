"""Git snapshots and shared path selection; never execute snapshot contents."""

import argparse
import os
import subprocess
from pathlib import Path

SOURCE = {".rs", ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".svelte"}
LOCKS = {"Cargo.lock", "uv.lock", "pnpm-lock.yaml", "package-lock.json"}
EXCLUDED = {"node_modules", "target", "dist", "vendor", "generated", "__pycache__"}
FIXTURES = {"fixtures", "inputs", "expected"}


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args])


def files(ref: str) -> list[str]:
    return git("ls-tree", "-r", "--name-only", "-z", ref).decode().split("\0")[:-1]


def contents(ref: str, path: str) -> str:
    return git("show", f"{ref}:{path}").decode("utf-8", errors="replace")


def excluded(path: str, fixtures: bool = False) -> bool:
    parts = set(Path(path).parts)
    return bool(parts & (EXCLUDED | (FIXTURES if fixtures else set()))) or path.startswith(
        (".agents/", ".codex/", "tools/cli-playground/src/cli_playground/static/")
    )


def source(path: str) -> bool:
    return Path(path).suffix in SOURCE and not excluded(path, fixtures=True)


def changes(base: str, head: str) -> dict[str, list[int]]:
    """Added/modified head line numbers, including empty entries for deleted files."""
    import re

    paths = git("diff", "--name-only", "--no-renames", "-z", base, head).decode().split("\0")[:-1]
    result: dict[str, list[int]] = {}
    for path in paths:
        patch = git("diff", "--no-ext-diff", "--no-renames", "--unified=0", base, head, "--", path)
        lines: list[int] = []
        for start, count in re.findall(rb"^@@ .* \+(\d+)(?:,(\d+))? @@", patch, re.MULTILINE):
            lines.extend(range(int(start), int(start) + int(count or b"1")))
        result[path] = lines
    return result


def summary(text: str) -> None:
    print(text)
    if output := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(output).open("a") as stream:
            stream.write(text + "\n")


def arguments() -> argparse.Namespace:
    """Read the merge-base comparison used by standalone guardrails."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="origin/master")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--report", type=Path, default=Path("target/coverage/lcov.info"))
    args = parser.parse_args()
    args.base = git("merge-base", args.base, args.head).decode().strip()
    return args
