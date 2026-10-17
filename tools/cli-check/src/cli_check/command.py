"""Validate repository CLI standards without building or executing packages."""

import argparse
import sys
from pathlib import Path

from cli_check.checks import benches, docs, layout, tests
from cli_check.workspace import discover, find_root

CHECKS = {
    "layout": layout.check,
    "tests": tests.check,
    "docs": docs.check,
    "benches": benches.check,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("group", nargs="?", choices=CHECKS, help="check group (default: all)")
    parser.add_argument("--root", type=Path, help="explicit Cargo workspace root")
    args = parser.parse_args()
    try:
        root = args.root.resolve() if args.root else find_root(Path.cwd())
        packages = discover(root)
        selected = [CHECKS[args.group]] if args.group else list(CHECKS.values())
        errors = sorted(error for check in selected for error in check(root, packages))
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Cannot check CLI standards: {error}", file=sys.stderr)
        return 1
    if errors:
        print("\n".join(map(str, errors)), file=sys.stderr)
        return 1
    print("CLI checks passed" + (f" ({args.group})" if args.group else ""))
    return 0
