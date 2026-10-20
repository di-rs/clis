"""Run commit checks for packages selected by the filenames supplied by Git hooks."""

import argparse
import os
import subprocess
import sys
from pathlib import Path

from cli_check.affected import lock_closures, production_change
from cli_check.workspace import discover, find_root, load_toml, strings, table, tables

RUST_CONFIG = {
    "Cargo.toml",
    "Cargo.lock",
    "rust-toolchain.toml",
    "rustfmt.toml",
    ".rustfmt.toml",
    "clippy.toml",
    ".clippy.toml",
    ".cargo/config.toml",
    ".config/nextest.toml",
}
PYTHON_CONFIG = {"pyproject.toml", "uv.lock", ".python-version", "ruff.toml", ".ruff.toml"}
UV = ["uv", "run", "--locked"]


def owner(path: str, directories: set[str]) -> str | None:
    matches = [directory for directory in directories if Path(path).is_relative_to(directory)]
    return max(matches, key=len, default=None)


def consumers(changed: set[str], dependencies: dict[str, set[str]]) -> set[str]:
    selected = set(changed)
    while added := {name for name, deps in dependencies.items() if deps & selected} - selected:
        selected.update(added)
    return selected


def rust_packages(root: Path, paths: list[str], *, dependents: bool) -> list[str]:
    packages = discover(root)
    directories = {p.directory.relative_to(root).as_posix(): p.name for p in packages}
    if RUST_CONFIG.intersection(paths):
        return sorted(directories.values())
    selected = {
        directories[directory]
        for path in paths
        if (directory := owner(path, set(directories))) is not None
    }
    changed = {
        name
        for directory, name in directories.items()
        if any(production_change(path, directory) for path in paths)
    }
    if dependents and changed:
        closures = lock_closures((root / "Cargo.lock").read_text())
        for name in directories.values():
            if name not in closures:
                raise ValueError(f"Package missing from Cargo.lock: {name}")
            if any(not source and dep in changed for dep, _, source in closures[name]):
                selected.add(name)
    return sorted(selected)


def python_packages(root: Path, paths: list[str], *, dependents: bool) -> list[str]:
    config = table(table(load_toml(root / "pyproject.toml")["tool"])["pytest"])
    testpaths = strings(config["testpaths"], "tool.pytest.testpaths")
    directories = {Path(path).parent.as_posix() for path in testpaths}
    records = tables(load_toml(root / "uv.lock")["package"])
    local: dict[str, str] = {}
    for record in records:
        source = table(record["source"])
        directory = source.get("editable", source.get("virtual"))
        if isinstance(directory, str) and directory != ".":
            local[str(record["name"])] = directory
            if owner(directory, directories) is None:
                directories.add(directory)
    if PYTHON_CONFIG.intersection(paths):
        return sorted(directories)
    selected = {directory for path in paths if (directory := owner(path, directories)) is not None}
    if not dependents or not selected:
        return sorted(selected)
    dependencies: dict[str, set[str]] = {directory: set() for directory in directories}
    for record in records:
        if record["name"] not in local:
            continue
        directory = owner(local[str(record["name"])], directories)
        assert directory is not None
        for dep in tables(record.get("dependencies", [])):
            if dep["name"] in local:
                target = owner(local[str(dep["name"])], directories)
                assert target is not None
                if target != directory:
                    dependencies[directory].add(target)
    # These tooling, test and composition imports are outside runtime metadata.
    extra = {
        "tools/cli-bench": {"tools/cli-check"},
        "tools/cli-playground": {"packages/boxie", "tools/cli-check"},
        "infra/environments/single-host": {"infra/host", "packages/boxie", "tools/cli-playground"},
    }
    for directory, deps in extra.items():
        if directory in dependencies:
            dependencies[directory].update(deps & directories)
    if "packages/boxie/tests/support/boxie_test_support/dependencies.py" in paths:
        return sorted(directories)
    changed = {
        directory
        for path in paths
        if (directory := owner(path, directories)) is not None
        and (
            not Path(path).is_relative_to(f"{directory}/tests")
            or path.startswith("packages/boxie/tests/support/")
            or path == "infra/host/tests/host_mocks.py"
        )
    }
    return sorted(selected | consumers(changed, dependencies))


def commands(root: Path, hook: str, paths: list[str]) -> list[list[str]]:
    if hook.startswith("cargo-") or hook == "cli-check":
        if hook == "cli-check" and "README.md" in paths:
            paths = [*paths, "Cargo.toml"]
        names = rust_packages(root, paths, dependents=hook in {"cargo-clippy", "cargo-nexttest"})
        if not names:
            return []
        args = [arg for name in names for arg in ("-p", name)]
        if hook == "cargo-fmt":
            return [["cargo", "fmt", *args, "--", "--check"]]
        if hook == "cargo-clippy":
            return [
                [
                    "cargo",
                    "clippy",
                    "--locked",
                    *args,
                    "--all-targets",
                    "--all-features",
                    "--",
                    "-D",
                    "warnings",
                    "-F",
                    "unsafe_code",
                ]
            ]
        if hook == "cargo-nexttest":
            return [
                [
                    "cargo",
                    "nextest",
                    "run",
                    "--locked",
                    *args,
                    "--hide-progress-bar",
                    "--failure-output",
                    "final",
                ]
            ]
        return [[*UV, "cli-check", *args]]
    directories = python_packages(root, paths, dependents=hook in {"mypy", "python-tests"})
    if not directories:
        return []
    if hook == "python-tests":
        tests = [
            f"{directory}/tests"
            for directory in directories
            if (root / directory / "tests").is_dir()
        ]
        return [[*UV, "pytest", *tests]] if tests else []
    if hook == "mypy":
        return [[*UV, "mypy", *directories]]
    if hook == "ruff-config":
        return [[*UV, "ruff", "check", *directories]]
    if hook == "ruff-format-config":
        return [[*UV, "ruff", "format", "--check", *directories]]
    raise ValueError(f"Unknown package hook: {hook}")


def mypy_environment(root: Path) -> dict[str, str]:
    config = table(table(load_toml(root / "pyproject.toml")["tool"])["pytest"])
    paths = [root / path for path in strings(config["testpaths"], "tool.pytest.testpaths")]
    for record in tables(load_toml(root / "uv.lock")["package"]):
        source = table(record["source"])
        directory = source.get("editable", source.get("virtual"))
        if isinstance(directory, str) and directory != ".":
            package = root / directory
            paths.append(package / "src" if (package / "src").is_dir() else package)
    # Resolve local imports without treating installed, unmarked packages as Any.
    search = [*map(str, paths), *filter(None, os.environ.get("MYPYPATH", "").split(os.pathsep))]
    return dict(os.environ, MYPYPATH=os.pathsep.join(dict.fromkeys(search)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "hook",
        choices=(
            "cargo-fmt",
            "cargo-clippy",
            "cargo-nexttest",
            "cli-check",
            "mypy",
            "python-tests",
            "ruff-config",
            "ruff-format-config",
        ),
    )
    parser.add_argument("paths", nargs="+")
    args = parser.parse_args()
    try:
        root = find_root(Path.cwd())
        for command in commands(root, args.hook, args.paths):
            print(" ".join(command), flush=True)
            environment = mypy_environment(root) if args.hook == "mypy" else None
            result = subprocess.run(command, cwd=root, env=environment, check=False)
            if result.returncode:
                return result.returncode
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Cannot run package hook: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
