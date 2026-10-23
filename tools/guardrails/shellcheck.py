"""Lint maintained files selected from the complete PR diff."""

import subprocess
from pathlib import Path

from .changes import arguments, changes, excluded, files


def lint(base: str, head: str) -> bool:
    changed = set(changes(base, head))
    configs = [
        ".shellcheckrc",
        ".github/workflows/shellcheck.yml",
        "tools/guardrails/shellcheck.py",
        "tools/guardrails/changes.py",
        "tools/guardrails/scope.py",
    ]
    all_files = True or bool(changed.intersection(configs))
    suffixes = [".sh", ".bash", ".shlib"]
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
                ["shellcheck", "--external-sources", "--"]
                + ["./" + path for path in paths[offset : offset + 100]],
                check=False,
            ).returncode
            == 0
        )
    return good


if __name__ == "__main__":
    args = arguments()
    raise SystemExit(not lint(args.base, args.head))
