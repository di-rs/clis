"""Advisory reports, using Git objects instead of executing the source tree."""

import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

from .changes import LOCKS, changes, contents, excluded, files, git, source, summary
from .syntax import script


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


def coverage(base: str, head: str, report: Path) -> None:
    changed = changes(base, head)
    hits_by_line: dict[tuple[str, int], bool] = {}
    path = ""
    root = Path.cwd()
    for line in report.read_text().splitlines():
        if line.startswith("SF:"):
            candidate = Path(line[3:])
            path = (
                str(candidate.relative_to(root))
                if candidate.is_absolute() and candidate.is_relative_to(root)
                else str(candidate)
            )
        elif line.startswith("DA:"):
            number, hits, *_ = line[3:].split(",")
            if path.endswith(".rs") and int(number) in changed.get(path, []):
                key = (path, int(number))
                hits_by_line[key] = hits_by_line.get(key, False) or int(hits) > 0
    covered, executable = sum(hits_by_line.values()), len(hits_by_line)
    ratio = f"{covered / executable:.1%}" if executable else "N/A (no instrumented changed lines)"
    summary(
        f"## Rust changed-line coverage (advisory)\n\n{covered}/{executable}: **{ratio}**. "
        "Only executable lines present in the LCOV report are counted; no minimum is enforced."
    )


def snapshot(ref: str, destination: Path) -> None:
    for path in files(ref):
        if source(path):
            target = destination / path
            if target.suffix == ".svelte":
                target = target.with_suffix(".svelte.ts")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(script(path, contents(ref, path)))


def clones(ref: str, destination: Path) -> Counter[str]:
    snapshot(ref, destination)
    subprocess.run(
        [
            "npx",
            "--yes",
            "jscpd@4.0.5",
            "--min-lines",
            "5",
            "--min-tokens",
            "50",
            "--reporters",
            "json",
            "--silent",
            "--output",
            str(destination / "report"),
            str(destination),
        ],
        check=True,
    )
    data = json.loads((destination / "report/jscpd-report.json").read_text())
    return Counter(
        hashlib.sha256(re.sub(r"\s+", " ", item["fragment"]).encode()).hexdigest()
        for item in data["duplicates"]
    )


def duplication(base: str, head: str) -> None:
    with TemporaryDirectory() as temp:
        before = clones(base, Path(temp) / "base")
        after = clones(head, Path(temp) / "head")
    count = sum((after - before).values())
    summary(
        f"## New duplication (advisory)\n\n**{count}** additional clone pairs "
        "(minimum 5 lines / 50 tokens). Existing clone pairs are subtracted by normalized "
        "fragment, so shifted line numbers do not count as new duplication. "
        "Generated code and designated fixture directories are excluded."
    )


def badge(output: Path) -> None:
    count = sum(
        sum(bool(line.strip()) for line in contents("HEAD", path).splitlines())
        for path in files("HEAD")
        if source(path)
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="220" height="20" role="img" '
        f'aria-label="source nonblank lines: {count}"><rect width="220" height="20" '
        'rx="3" fill="#374151"/><text x="110" y="14" fill="white" text-anchor="middle" '
        f'font-family="Verdana,sans-serif" font-size="11">source nonblank lines: {count}</text></svg>\n'
    )
