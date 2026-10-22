"""Git snapshots and shared path selection; never execute snapshot contents."""

import fnmatch
import json
import os
import subprocess
from pathlib import Path

SOURCE = {".rs", ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".svelte"}
LOCKS = {"Cargo.lock", "uv.lock", "pnpm-lock.yaml", "package-lock.json"}
EXCLUDED = {"node_modules", "target", "dist", "vendor", "generated", "__pycache__"}
FIXTURES = {"fixtures", "inputs", "expected"}


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args])


def files(ref: str) -> list[str]:
    return git("ls-tree", "-r", "--name-only", "-z", ref).decode().split("\0")[:-1]


def contents(ref: str, path: str) -> str:
    return git("show", f"{ref}:{path}").decode("utf-8", errors="replace")


def excluded(path: str, fixtures: bool = False) -> bool:
    parts = set(Path(path).parts)
    return bool(parts & (EXCLUDED | (FIXTURES if fixtures else set()))) or path.startswith(
        (".agents/", ".codex/", "tools/cli-playground/src/cli_playground/static/")
    )


def source(path: str) -> bool:
    return Path(path).suffix in SOURCE and not excluded(path, fixtures=True)


def changes(base: str, head: str) -> dict[str, list[int]]:
    """Added/modified head line numbers, including empty entries for deleted files."""
    import re

    paths = git("diff", "--name-only", "--no-renames", "-z", base, head).decode().split("\0")[:-1]
    result: dict[str, list[int]] = {}
    for path in paths:
        patch = git("diff", "--no-ext-diff", "--no-renames", "--unified=0", base, head, "--", path)
        lines: list[int] = []
        for start, count in re.findall(rb"^@@ .* \+(\d+)(?:,(\d+))? @@", patch, re.MULTILINE):
            lines.extend(range(int(start), int(start) + int(count or b"1")))
        result[path] = lines
    return result


def matches(paths: list[str], patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for path in paths for pattern in patterns)


def selection(paths: list[str], present: list[str]) -> dict[str, bool]:
    maintained = [p for p in present if not excluded(p)]
    config = matches(
        paths,
        (
            "tools/guardrails/*",
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
                "tools/cli-playground/tests/test_browser.py",
                "tools/cli-playground/src/*",
                "packages/boxie/src/*",
            ),
            {".ts", ".js", ".svelte"},
        ),
        "source": (config or any(source(path) for path in paths))
        and any(source(path) for path in present),
        "shell": selected(
            ("*.sh", "*.bash", "*.shlib", ".shellcheckrc"), {".sh", ".bash", ".shlib"}
        ),
        "yaml": selected(("*.yml", "*.yaml", ".yamllint*"), {".yml", ".yaml"}),
        "markdown": selected(
            ("*.md", "*.markdown", ".markdownlint*", "lychee.toml", "typos.toml", "prek.toml"),
            {".md", ".markdown"},
        ),
        "spelling": bool(maintained) and any(not excluded(path) for path in paths),
        "python_audit": selected(("*pyproject.toml", "uv.lock"), {".lock"})
        and "uv.lock" in present,
        "frontend_audit": selected(
            ("*package.json", "*pnpm-lock.yaml", "*pnpm-workspace.yaml"), {".yaml"}
        )
        and any(path.endswith("pnpm-lock.yaml") for path in present),
        "actions": matches(
            paths, (".github/*", "*action.yml", "*action.yaml", "tools/guardrails/*")
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


def summary(text: str) -> None:
    print(text)
    if output := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(output).open("a") as stream:
            stream.write(text + "\n")
