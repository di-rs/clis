"""Report PR additions and deletions without imposing a merge gate."""

from pathlib import Path

from .changes import LOCKS, arguments, changes, excluded, git, summary


def size(base: str, head: str) -> None:
    total = review = 0
    for path in changes(base, head):
        stats = git("diff", "--numstat", "--no-renames", base, head, "--", path).decode()
        for line in stats.splitlines():
            adds, deletes, _ = line.split("\t", 2)
            count = int(adds) + int(deletes) if adds != "-" else 0
            total += count
            if not excluded(path) and Path(path).name not in LOCKS:
                review += count
    summary(
        f"## PR size (advisory)\n\nTotal text lines changed: **{total}**. "
        f"Review lines: **{review} / 200** — "
        + ("above the advisory limit." if review > 200 else "within the advisory limit.")
        + "\n\nAdditions + deletions; binary files have no line count. "
        "Review count excludes lockfiles, generated and vendored paths."
    )


if __name__ == "__main__":
    args = arguments()
    size(args.base, args.head)
