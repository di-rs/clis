"""Lint maintained files selected from the complete PR diff."""

import subprocess
from pathlib import Path

from .changes import arguments, changes, excluded, files


def lint(base: str, head: str) -> bool:
    changed = set(changes(base, head))
    configs = [
        ".markdownlint-cli2.jsonc",
        ".github/workflows/markdown-lint.yml",
        "tools/guardrails/markdown_lint.py",
        "tools/guardrails/changes.py",
        "tools/guardrails/scope.py",
    ]
    all_files = False or bool(changed.intersection(configs))
    suffixes = [".md", ".markdown"]
    paths = [
        path
        for path in files(head)
        if not excluded(path)
        and Path(path).is_file()
        and not Path(path).is_symlink()
        and (not suffixes or Path(path).suffix in suffixes)
        and (all_files or path in changed)
    ]
    good = True
    for offset in range(0, len(paths), 100):
        good &= (
            subprocess.run(
                ["npx", "--yes", "markdownlint-cli2@0.22.0", "--config", ".markdownlint-cli2.jsonc"]
                + ["./" + path for path in paths[offset : offset + 100]],
                check=False,
            ).returncode
            == 0
        )
    return good


if __name__ == "__main__":
    args = arguments()
    raise SystemExit(not lint(args.base, args.head))
