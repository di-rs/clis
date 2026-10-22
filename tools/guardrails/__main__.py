"""Run one scoped guardrail from the repository root."""

import argparse
import subprocess
from pathlib import Path

from .changes import changes, excluded, files, git, select


def lint_paths(kind: str, base: str, head: str) -> list[str]:
    suffixes = {
        "shell": {".sh", ".bash", ".shlib"},
        "yaml": {".yaml", ".yml"},
        "markdown": {".md", ".markdown"},
    }
    paths = [
        path
        for path in files(head)
        if not excluded(path) and Path(path).is_file() and not Path(path).is_symlink()
    ]
    if kind in suffixes:
        paths = [path for path in paths if Path(path).suffix in suffixes[kind]]
    changed = set(changes(base, head))
    own_config = {
        "yaml": ".yamllint.yml",
        "markdown": ".markdownlint-cli2.jsonc",
        "spelling": "typos.toml",
    }
    all_files = kind == "shell" or own_config.get(kind) in changed
    all_files |= any(
        path.startswith(("tools/guardrails/", ".github/workflows/guardrails")) for path in changed
    )
    return paths if all_files else [path for path in paths if path in changed]


def lint(kind: str, base: str, head: str) -> bool:
    selected = lint_paths(kind, base, head)
    commands = {
        "shell": ["shellcheck", "--external-sources", "--"],
        "yaml": ["yamllint", "-c", ".yamllint.yml", "--"],
        "markdown": [
            "npx",
            "--yes",
            "markdownlint-cli2@0.22.0",
            "--config",
            ".markdownlint-cli2.jsonc",
        ],
        "spelling": ["typos", "--force-exclude", "--"],
    }
    command = commands[kind]
    good = True
    for offset in range(0, len(selected), 100):
        good &= (
            subprocess.run(
                command + ["./" + p for p in selected[offset : offset + 100]], check=False
            ).returncode
            == 0
        )
    return good


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "mode",
        choices=[
            "select",
            "policy",
            "size",
            "coverage",
            "duplication",
            "badge",
            "shell",
            "yaml",
            "markdown",
            "spelling",
            "python-audit",
            "frontend-audit",
        ],
    )
    parser.add_argument("--base", default="origin/master")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--report", type=Path, default=Path("target/coverage/lcov.info"))
    args = parser.parse_args()
    base = git("merge-base", args.base, args.head).decode().strip()
    if args.mode == "select":
        select(base, args.head)
    elif args.mode == "policy":
        from .policy import check

        return int(not check(base, args.head))
    elif args.mode.endswith("-audit"):
        from .audits import audit

        return int(not audit(args.mode.removesuffix("-audit")))
    elif args.mode in {"shell", "yaml", "markdown", "spelling"}:
        return int(not lint(args.mode, base, args.head))
    else:
        from . import reports

        if args.mode == "coverage":
            reports.coverage(base, args.head, args.report)
        elif args.mode == "badge":
            reports.badge(args.report)
        elif args.mode == "size":
            reports.size(base, args.head)
        else:
            reports.duplication(base, args.head)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
