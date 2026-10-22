"""Validate repository CLI standards without building or executing packages."""

import sys
from dataclasses import asdict
from pathlib import Path

from cli_check.checks import benches, docs, layout, tests
from cli_check.output import ArgumentParser, emit, failure
from cli_check.workspace import discover, find_root

CHECKS = {
    "layout": layout.check,
    "tests": tests.check,
    "docs": docs.check,
    "benches": benches.check,
}


def main() -> int:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("group", nargs="?", choices=CHECKS, help="check group (default: all)")
    parser.add_argument("--root", type=Path, help="explicit Cargo workspace root")
    parser.add_argument("-p", "--package", action="append", help="check only this Cargo package")
    parser.add_argument("--json", action="store_true", help="emit structured results and errors")
    parser.add_argument(
        "--list-packages", action="store_true", help="discover packages without checking them"
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    args = parser.parse_args()
    try:
        root = args.root.resolve() if args.root else find_root(Path.cwd())
        packages = discover(root)
        if args.package:
            unknown = set(args.package) - {package.name for package in packages}
            if unknown:
                raise ValueError(f"Unknown packages: {', '.join(sorted(unknown))}")
            packages = [package for package in packages if package.name in args.package]
        if args.list_packages:
            records = [
                {"name": p.name, "path": str(p.directory), "cli": p.is_cli} for p in packages
            ]
            if args.json:
                emit({"packages": records})
            else:
                print("\n".join(p.name for p in packages))
            return 0
        selected = [CHECKS[args.group]] if args.group else list(CHECKS.values())
        errors = sorted(error for check in selected for error in check(root, packages))
    except (OSError, ValueError, KeyError, TypeError) as error:
        if args.json:
            failure("operation", str(error))
        else:
            print(f"Cannot check CLI standards: {error}", file=sys.stderr)
        return 1
    if args.json:
        emit(
            {
                "root": str(root),
                "packages": [p.name for p in packages],
                "checks": [args.group] if args.group else list(CHECKS),
                "diagnostics": [asdict(error) for error in errors],
            },
            ok=not errors,
        )
        return int(bool(errors))
    if errors:
        print("\n".join(map(str, errors)), file=sys.stderr)
        return 1
    print("CLI checks passed" + (f" ({args.group})" if args.group else ""))
    return 0
