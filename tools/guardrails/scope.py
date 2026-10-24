"""Select a check using the full Git diff and maintained file types at HEAD."""

import argparse
import fnmatch
import os
from pathlib import Path

from .changes import changes, excluded, files, git


def matches(paths: list[str], patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for path in paths for pattern in patterns)


def selected(
    paths: list[str], present: list[str], patterns: list[str], kinds: list[str], configs: list[str]
) -> bool:
    maintained = [path for path in present if not excluded(path)]
    changed = [path for path in paths if not excluded(path)]
    return matches(maintained, kinds) and (matches(changed, patterns) or matches(paths, configs))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--patterns", nargs="+", required=True)
    parser.add_argument("--present", nargs="+", required=True)
    parser.add_argument("--config", nargs="+", default=[])
    args = parser.parse_args()
    base = git("merge-base", args.base, "HEAD").decode().strip()
    result = selected(
        list(changes(base, "HEAD")), files("HEAD"), args.patterns, args.present, args.config
    )
    print(f"selected={str(result).lower()}")
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a") as stream:
            print(f"selected={str(result).lower()}", file=stream)
            print(f"base={base}", file=stream)


if __name__ == "__main__":
    main()
