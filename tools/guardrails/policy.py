"""Compare function metrics and source exceptions with the PR merge base."""

import re
from collections import Counter, defaultdict

from tree_sitter import Node

from .changes import changes, contents, files, source, summary
from .syntax import Function, functions, parse, script, value, walk

LIMITS = {"complexity": 15, "depth": 4, "length": 100}
SUPPRESSION = re.compile(
    r"\bnoqa\b|\btype:\s*ignore\b|@ts-(?:ignore|expect-error|nocheck)|"
    r"eslint-disable(?:-next-line|-line)?|svelte-ignore|"
    r"#\s*!?\[\s*(?:allow|expect|ignore|cfg_attr)\b|"
    r"(?:pytest\.mark\.|unittest\.)(?:skip|skipif|skipIf|skipUnless|expectedFailure)\b|"
    r"(?:test|it|describe)\.(?:skip|skipIf|runIf|todo)\b|pytest\.skip\b"
)
REASON = re.compile(r"\breason\s*[:=]\s*[\"']?([^\n\"']+)", re.IGNORECASE)
BLANKET = re.compile(
    r"#!\[\s*(?:allow|expect)|@ts-nocheck|eslint-disable(?!-next-line|-line)|(?:ruff|flake8):\s*noqa"
)


def meaningful(text: str) -> bool:
    return len(text.strip().split()) >= 3


def reason(text: str) -> bool:
    return any(meaningful(match.group(1)) for match in REASON.finditer(text))


def directive(node: Node, data: bytes) -> str | None:
    if node.type not in {
        "comment",
        "line_comment",
        "block_comment",
        "attribute_item",
        "inner_attribute_item",
        "decorator",
        "call",
        "call_expression",
    }:
        return None
    raw = value(node, data)
    if node.type == "decorator":
        expression = node.named_children[0]
        decorated = expression.child_by_field_name("function") or expression
        if not SUPPRESSION.search(value(decorated, data)):
            return None
    if node.type in {"call", "call_expression"}:
        callee = node.child_by_field_name("function")
        if callee is None or not SUPPRESSION.search(value(callee, data)):
            return None
        if node.parent and node.parent.type == "decorator":
            return None
    if node.type in {"attribute_item", "inner_attribute_item"} and "cfg_attr" in raw:
        if not re.search(r"\b(?:allow|expect|ignore)\s*[(,)]", raw):
            return None
    return raw if SUPPRESSION.search(raw) else None


def is_blanket(path: str, node: Node, raw: str) -> bool:
    blanket = bool(BLANKET.search(raw))
    blanket |= node.type == "inner_attribute_item" and bool(re.search(r"\b(?:allow|expect)\b", raw))
    blanket |= bool(re.search(r"\bnoqa\b(?!\s*:\s*[A-Z]+\d)", raw))
    blanket |= bool(re.search(r"type:\s*ignore(?!\s*\[)", raw))
    blanket |= bool(
        re.search(r"\b(?:allow|expect)\s*\(\s*(?:warnings|clippy::all|clippy::pedantic)\b", raw)
    )
    # Outer attributes on Rust modules suppress all members, not just one item.
    following = node.next_named_sibling
    while following and following.type in {"attribute_item", "line_comment", "block_comment"}:
        following = following.next_named_sibling
    if path.endswith(".rs") and re.search(r"\b(?:allow|expect)\s*\(", raw):
        blanket |= following is not None and following.type == "mod_item"
    return blanket


def exceptions(path: str, text: str) -> list[tuple[int, int, str, bool, bool]]:
    """Return spans, normalized directive, reason validity, and blanket status."""
    result: list[tuple[int, int, str, bool, bool]] = []
    data = script(path, text).encode()
    nodes = list(walk(parse(path, text)))
    comments = [node for node in nodes if "comment" in node.type]
    for node in nodes:
        raw = directive(node, data)
        if raw is None:
            continue
        start, end = node.start_point.row + 1, node.end_point.row + 1
        adjacent = "\n".join(
            value(comment, data)
            for comment in comments
            if start - 2 <= comment.end_point.row <= end - 1
        )
        valid = reason(raw) or reason(adjacent)
        # Standard skip decorators accept a literal reason as their argument.
        if re.search(r"(?:unittest\.skip(?:If|Unless)?|pytest\.skip)\s*\(|#\[ignore\s*=", raw):
            valid |= any(meaningful(s) for s in re.findall(r"[\"']([^\"']+)[\"']", raw))
        blanket = is_blanket(path, node, raw)
        result.append((start, end, " ".join(raw.split()), valid, blanket))
    return result


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


def compare_exceptions(path: str, before: str, after: str) -> tuple[list[str], int, int]:
    old = Counter(item[2] for item in exceptions(path, before))
    new = exceptions(path, after)
    counts = Counter(item[2] for item in new)
    failures: list[str] = []
    remaining = old.copy()
    for start, _, directive, valid, blanket in new:
        if remaining[directive]:
            remaining[directive] -= 1
            continue
        if blanket or not valid:
            problem = (
                "blanket suppression is forbidden"
                if blanket
                else "missing adjacent reason: explanation"
            )
            failures.append(f"{path}:{start}: {problem}")
    return failures, sum((counts - old).values()), sum((old - counts).values())


def check(base: str, head: str) -> bool:
    present = set(files(head))
    previous = set(files(base))
    failures: list[str] = []
    reports: list[str] = []
    added = removed = 0
    for path in changes(base, head):
        if not source(path):
            continue
        before = contents(base, path) if path in previous else ""
        after = contents(head, path) if path in present else ""
        bad, advisory = compare_functions(path, before, after)
        exceptions_bad, plus, minus = compare_exceptions(path, before, after)
        failures.extend(bad + exceptions_bad)
        reports.extend(advisory)
        added += plus
        removed += minus
    summary(
        f"## Source policy\n\nExceptions added: {added}; removed: {removed}.\n\n"
        + "\n".join(f"- {line}" for line in failures)
        + "\n\n### Test complexity (advisory)\n\n"
        + ("\n".join(f"- {line}" for line in reports) or "No regressions.")
    )
    return not failures
