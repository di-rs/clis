"""Report coverage of changed executable Rust lines."""

from pathlib import Path

from .changes import arguments, changes, summary


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


if __name__ == "__main__":
    args = arguments()
    coverage(args.base, args.head, args.report)
