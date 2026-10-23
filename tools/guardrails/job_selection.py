"""Select existing language jobs without omitting integration consumers."""

import fnmatch
import json
import os
from pathlib import Path

from .changes import arguments, changes, excluded, files, git


def matches(paths: list[str], patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for path in paths for pattern in patterns)


def selection(paths: list[str], present: list[str]) -> dict[str, bool]:
    maintained = [p for p in present if not excluded(p)]
    config = matches(
        paths,
        (
            "tools/guardrails/job_selection.py",
            "tools/guardrails/changes.py",
            ".github/workflows/guardrails.yml",
            ".github/workflows/pull-request-check.yml",
            ".github/workflows/codeql.yml",
            "prek.toml",
        ),
    )

    def selected(patterns: tuple[str, ...], suffixes: set[str]) -> bool:
        return (config or matches(paths, patterns)) and any(
            Path(path).suffix in suffixes for path in maintained
        )

    return {
        "rust": selected(
            (
                "*.rs",
                "*Cargo.toml",
                "*Cargo.lock",
                "*rust-toolchain*",
                ".cargo/*",
                "clippy.toml",
                "deny.toml",
                ".config/nextest.toml",
                "buildins/*/tests/*",
                "utils/*/tests/*",
                "packages/biggie/tests/*",
                "packages/kara/tests/*",
                "packages/parsu/tests/*",
            ),
            {".rs"},
        ),
        "python": selected(
            (
                "*.py",
                "*.pyi",
                "*pyproject.toml",
                "uv.lock",
                ".python-version",
                "packages/boxie/tests/*",
                "tools/cli-playground/tests/*",
            ),
            {".py", ".pyi"},
        ),
        "frontend": selected(
            (
                "tools/cli-playground/frontend/*",
                "tools/cli-playground/tests/*",
                "tools/cli-playground/pyproject.toml",
                "packages/boxie/pyproject.toml",
                "pyproject.toml",
                "uv.lock",
                "tools/cli-playground/src/*",
                "packages/boxie/src/*",
            ),
            {".ts", ".js", ".svelte"},
        ),
        "markdown": selected(
            ("*.md", "*.markdown", ".markdownlint*", "lychee.toml", "typos.toml", "prek.toml"),
            {".md", ".markdown"},
        ),
        "actions": matches(
            paths, (".github/*", "*action.yml", "*action.yaml", "tools/guardrails/job_selection.py")
        ),
    }


def select(base: str, head: str) -> None:
    base = git("merge-base", base, head).decode().strip()
    present = files(head)
    paths = (
        present
        if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        else list(changes(base, head))
    )
    result = selection(paths, present)
    languages = [name for name in ("rust", "python", "actions") if result[name]]
    print(json.dumps(result, indent=2))
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a") as stream:
            for key, value in result.items():
                print(f"{key}={str(value).lower()}", file=stream)
            print(f"languages={json.dumps(languages)}", file=stream)
            print(f"base={base}", file=stream)


if __name__ == "__main__":
    args = arguments()
    select(args.base, args.head)
