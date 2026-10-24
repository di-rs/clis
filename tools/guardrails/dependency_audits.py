"""Fail on advisories; distinguish an unavailable service from a clean audit."""

import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .changes import summary


def python_count(dependencies: Any) -> int | None:
    if not isinstance(dependencies, list):
        return None
    valid = [d for d in dependencies if isinstance(d, dict) and isinstance(d.get("vulns"), list)]
    count = sum(len(d["vulns"]) for d in valid)
    return count if count or len(valid) == len(dependencies) else None


def advisory_count(kind: str, data: Any) -> int | None:
    if not isinstance(data, dict):
        return None
    if kind == "python":
        return python_count(data.get("dependencies"))
    metadata = data.get("metadata", {})
    vulnerabilities = metadata.get("vulnerabilities") if isinstance(metadata, dict) else None
    if not isinstance(vulnerabilities, dict) or not vulnerabilities:
        return None
    values = list(vulnerabilities.values())
    if not all(isinstance(n, int) and n >= 0 for n in values):
        return None
    return sum(int(number) for number in values)


def verdict(kind: str, data: Any, status: int) -> tuple[str, int]:
    count = advisory_count(kind, data)
    if count:
        return "vulnerable", count
    if count == 0 and status == 0 and not data.get("error"):
        return "clean", 0
    return "incomplete", 0


def audit(kind: str) -> bool:
    with TemporaryDirectory() as temp:
        directory = Path(temp)
        if kind == "python":
            export = subprocess.run(
                [
                    "uv",
                    "export",
                    "--frozen",
                    "--all-packages",
                    "--all-groups",
                    "--no-emit-workspace",
                    "--no-hashes",
                    "--output-file",
                    str(directory / "requirements.txt"),
                ],
                capture_output=True,
                text=True,
            )
            if export.returncode:
                summary(
                    "## Python audit\n\n**Incomplete**: dependency export failed. No clean result."
                )
                return True
            command = [
                "pip-audit",
                "--disable-pip",
                "--no-deps",
                "--progress-spinner",
                "off",
                "--format",
                "json",
                "--requirement",
                str(directory / "requirements.txt"),
            ]
        else:
            frontend = Path("tools/cli-playground/frontend")
            for name in ("package.json", "pnpm-lock.yaml"):
                (directory / name).write_bytes((frontend / name).read_bytes())
            # Do not inherit per-repository audit ignores or registry-error bypasses.
            (directory / "pnpm-workspace.yaml").write_text("packages: []\n")
            command = ["pnpm", "audit", "--json", "--ignore-registry-errors=false"]
        try:
            result = subprocess.run(
                command,
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=300,
                env=os.environ
                | {"NPM_CONFIG_USERCONFIG": os.devnull, "NPM_CONFIG_GLOBALCONFIG": os.devnull},
            )
            data = json.loads(result.stdout)
            state, count = verdict(kind, data, result.returncode)
        except subprocess.TimeoutExpired, OSError, ValueError:
            state, count = "incomplete", 0
        summary(
            f"## {kind.title()} dependency audit\n\n**{state}**; known advisories: {count}. "
            "No vulnerability exceptions are permitted. Incomplete audits are advisory, "
            "not evidence that dependencies are clean."
        )
        if state == "vulnerable":
            # Dependency advisory JSON contains package metadata, not repository secrets.
            print(json.dumps(data, indent=2))
        return state != "vulnerable"


if __name__ == "__main__":
    import sys

    raise SystemExit(not audit(sys.argv[1]))
