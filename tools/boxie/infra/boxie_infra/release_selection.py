"""Select runtime changes relative to the last successful host revision."""

import io
import os
import re
import subprocess
import sys
import tarfile
from pathlib import Path
from tempfile import TemporaryDirectory

WORKSPACE_MANIFESTS = {
    "pyproject.toml",
    "infra/host/pyproject.toml",
    "tools/boxie/tests/support/pyproject.toml",
    "tools/boxie/infra/pyproject.toml",
    "tools/cli-playground/infra/pyproject.toml",
    "tools/cli-bench/pyproject.toml",
    "tools/cli-check/pyproject.toml",
}


def runtime_requirements(source: Path, package: str) -> bytes:
    """Export the locked production requirements without local workspace paths."""
    if package not in {"boxie", "cli-playground"}:
        raise ValueError("Unknown runtime package")
    return subprocess.check_output(
        [
            "uv",
            "export",
            "--locked",
            "--package",
            package,
            "--no-dev",
            "--no-header",
            "--no-annotate",
            "--no-emit-workspace",
            "--offline",
        ],
        cwd=source,
        env={**os.environ, "UV_CACHE_DIR": str(source.parent / "uv-cache")},
        stderr=subprocess.PIPE,
    )


def _snapshot(revision: str, target: Path) -> None:
    archive = subprocess.check_output(["git", "archive", "--format=tar", revision])
    with tarfile.open(fileobj=io.BytesIO(archive)) as contents:
        contents.extractall(target, filter="data")


def changed(app: str, base: str) -> tuple[bool, bool]:
    if app not in {"boxie", "playground"}:
        raise ValueError("Unknown application")
    if not base:
        return True, app == "playground"
    if not re.fullmatch("[a-f0-9]{40}", base):
        raise ValueError("Use a full successful revision")
    subprocess.run(["git", "merge-base", "--is-ancestor", base, "HEAD"], check=True)
    paths = set(
        subprocess.check_output(
            ["git", "diff", "--name-only", base, "HEAD"], text=True
        ).splitlines()
    )
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
    if app == "boxie":
        runtime_files.add("infra/compose.observability.yml")
    runtime = any(
        path in WORKSPACE_MANIFESTS | {".python-version", ".dockerignore"}
        or path.startswith(tuple(prefix + part for part in runtime_prefixes))
        or path in {prefix + part for part in runtime_files}
        or path.startswith(
            prefix
            + ("infra/Runtime.Dockerfile" if app == "boxie" else "infra/Playground.Dockerfile")
        )
        for path in paths
    )
    sandbox = app == "playground" and any(
        path in {".dockerignore", prefix + "shell.sh"}
        or path.startswith(prefix + "Sandbox.Dockerfile")
        or path.startswith(prefix + "fixtures/")
        for path in paths
    )
    if "uv.lock" in paths:
        with TemporaryDirectory(prefix="clis-runtime-export-") as temporary:
            root = Path(temporary)
            before, after = root / "before", root / "after"
            before.mkdir()
            after.mkdir()
            _snapshot(base, before)
            _snapshot("HEAD", after)
            package = "boxie" if app == "boxie" else "cli-playground"
            runtime |= runtime_requirements(before, package) != runtime_requirements(after, package)
    return runtime, sandbox


if __name__ == "__main__":
    runtime, sandbox = changed(sys.argv[1], sys.argv[2])
    print(f"runtime={str(runtime).lower()}\nsandbox={str(sandbox).lower()}")
