"""Select runtime changes relative to the last successful host revision."""

import re
import subprocess
import sys
import tomllib
from typing import Any


def dependency_closure(lock: dict[str, Any], name: str) -> list[dict[str, Any]]:
    pending, seen, result = [name], set(), []
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        for package in lock["package"]:
            if package["name"] == current:
                result.append(
                    {
                        key: value
                        for key, value in package.items()
                        if key not in {"dev-dependencies", "optional-dependencies", "metadata"}
                    }
                )
                pending.extend(item["name"] for item in package.get("dependencies", []))
    return sorted(result, key=lambda item: (item["name"], item.get("version", "")))


def changed(app: str, base: str) -> tuple[bool, bool]:
    if app not in {"boxie", "playground"}:
        raise ValueError("Unknown application")
    if not base:
        return True, app == "playground"
    if not re.fullmatch("[a-f0-9]{40}", base):
        raise ValueError("Use a full successful revision")
    paths = subprocess.check_output(
        ["git", "diff", "--name-only", base, "HEAD"], text=True
    ).splitlines()
    prefix = "tools/boxie/" if app == "boxie" else "tools/cli-playground/"
    runtime_prefixes = ("src/", "alembic/") if app == "boxie" else ("src/", "frontend/")
    runtime_files = {
        "pyproject.toml",
        "infra/deploy.sh",
        "infra/compose.production.yml",
        "infra/compose.operations.yml",
        "infra/Runtime.Dockerfile" if app == "boxie" else "infra/Playground.Dockerfile",
    }
    runtime = any(
        path == ".python-version"
        or path.startswith(tuple(prefix + part for part in runtime_prefixes))
        or path in {prefix + part for part in runtime_files}
        for path in paths
    )
    sandbox = app == "playground" and any(
        path in {prefix + "Sandbox.Dockerfile", prefix + "shell.sh"}
        or path.startswith(prefix + "fixtures/")
        for path in paths
    )
    if "uv.lock" in paths:
        before = tomllib.loads(
            subprocess.check_output(["git", "show", f"{base}:uv.lock"], text=True)
        )
        after = tomllib.loads(subprocess.check_output(["git", "show", "HEAD:uv.lock"], text=True))
        package = "boxie" if app == "boxie" else "cli-playground"
        runtime |= dependency_closure(before, package) != dependency_closure(after, package)
    return runtime, sandbox


if __name__ == "__main__":
    runtime, sandbox = changed(sys.argv[1], sys.argv[2])
    print(f"runtime={str(runtime).lower()}\nsandbox={str(sandbox).lower()}")
