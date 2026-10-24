"""Enforce function metrics without worsening existing over-limit code."""

from collections import defaultdict

from .changes import arguments, changes, contents, files, source, summary
from .syntax import Function, functions

LIMITS = {"complexity": 15, "depth": 4, "length": 100}


def compare_functions(path: str, before: str, after: str) -> tuple[list[str], list[str]]:
    old: dict[str, list[Function]] = defaultdict(list)
    for function in functions(path, before):
        old[function.name].append(function)
    failures: list[str] = []
    reports: list[str] = []
    for function in functions(path, after):
        previous = old[function.name].pop(0) if old[function.name] else None
        for metric, limit in LIMITS.items():
            current = getattr(function, metric)
            allowed = max(limit, getattr(previous, metric)) if previous else limit
            # If an existing function exceeds any limit, no metric may worsen.
            if previous and any(getattr(previous, key) > cap for key, cap in LIMITS.items()):
                allowed = getattr(previous, metric)
            if current <= allowed:
                continue
            message = (
                f"{path}:{function.start}: {function.name} {metric}={current} exceeds {allowed}"
            )
            (reports if function.test else failures).append(message)
    return failures, reports


def check(base: str, head: str) -> bool:
    present, previous = set(files(head)), set(files(base))
    failures: list[str] = []
    reports: list[str] = []
    for path in changes(base, head):
        if not source(path):
            continue
        before = contents(base, path) if path in previous else ""
        after = contents(head, path) if path in present else ""
        bad, advisory = compare_functions(path, before, after)
        failures.extend(bad)
        reports.extend(advisory)
    summary(
        "## Function complexity\n\n"
        + "\n".join(failures)
        + "\n\n### Tests (advisory)\n\n"
        + ("\n".join(reports) or "No regressions.")
    )
    return not failures


if __name__ == "__main__":
    args = arguments()
    raise SystemExit(not check(args.base, args.head))
