"""Select runtime changes relative to the last successful host revision."""

import json
import re
import subprocess
import sys
import tomllib
from typing import Any


def dependency_closure(lock: dict[str, Any], name: str) -> list[dict[str, Any]]:
    pending: list[dict[str, Any]] = [{"name": name}]
    seen: set[tuple[str, tuple[str, ...]]] = set()
    result: dict[str, dict[str, Any]] = {}
    while pending:
        dependency = pending.pop()
        current = dependency["name"]
        extras = tuple(sorted(dependency.get("extra", [])))
        if (current, extras) in seen:
            continue
        seen.add((current, extras))
        for package in lock["package"]:
            if package["name"] != current:
                continue
            key = json.dumps(
                [current, package.get("version"), package.get("source")], sort_keys=True
            )
            selected = result.setdefault(
                key,
                {
                    key: value
                    for key, value in package.items()
                    if key not in {"dev-dependencies", "optional-dependencies", "metadata"}
                },
            )
            pending.extend(package.get("dependencies", []))
            for extra in extras:
                dependencies = package.get("optional-dependencies", {}).get(extra, [])
                selected.setdefault("optional-dependencies", {})[extra] = dependencies
                pending.extend(dependencies)
    return [result[key] for key in sorted(result)]


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
        "infra/boxie_infra/smoke.py",
        "infra/boxie_infra/terminal.py",
        "infra/playground_infra/installation.py",
        "infra/Runtime.Dockerfile" if app == "boxie" else "infra/Playground.Dockerfile",
    }
    runtime = any(
        path in {".python-version", ".dockerignore"}
        or path.startswith(tuple(prefix + part for part in runtime_prefixes))
        or path in {prefix + part for part in runtime_files}
        or path.startswith(
            prefix
            + ("infra/Runtime.Dockerfile" if app == "boxie" else "infra/Playground.Dockerfile")
        )
        for path in paths
    )
    sandbox = app == "playground" and any(
        path == prefix + "shell.sh"
        or path.startswith(prefix + "Sandbox.Dockerfile")
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
