#!/usr/bin/env python3.15
"""Check the repository's declared CLI test layout, without evaluating Rust tests.

Discovery expands the root workspace's members and excludes, plus its root package.
It recognizes explicit binary targets and Cargo's ordinary binary source locations.
It deliberately does not resolve Cargo's implicit path-dependency workspace members;
repository packages must be covered by the root members list, as they are today.
"""

import argparse
import sys
import tomllib
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[2]


def load_toml(path: Path) -> dict[str, object]:
    with path.open("rb") as source:
        return cast(dict[str, object], tomllib.load(source))


def workspace_members(root: Path) -> list[Path]:
    manifest = load_toml(root / "Cargo.toml")
    workspace = cast(dict[str, list[str]], manifest["workspace"])
    members = {path for pattern in workspace.get("members", []) for path in root.glob(pattern)}
    excluded = {path for pattern in workspace.get("exclude", []) for path in root.glob(pattern)}
    if "package" in manifest:
        members.add(root)
    return sorted(members - excluded)


def has_binary(directory: Path, manifest: dict[str, object]) -> bool:
    if manifest.get("bin"):
        return True
    package = cast(dict[str, object], manifest.get("package", {}))
    if package.get("autobins") is False:
        return False
    return (directory / "src/main.rs").is_file() or any(
        path.is_file()
        for pattern in ("src/bin/*.rs", "src/bin/*/main.rs")
        for path in directory.glob(pattern)
    )


def cli_test_enabled(directory: Path, manifest: dict[str, object]) -> bool:
    targets = cast(list[dict[str, object]], manifest.get("test", []))
    entrypoint = (directory / "tests/cli.rs").resolve()
    explicit = [
        target
        for target in targets
        if (directory / cast(str, target.get("path", f"tests/{target['name']}.rs"))).resolve()
        == entrypoint
    ]
    if explicit:
        return any(target.get("test") is not False for target in explicit)
    package = cast(dict[str, object], manifest.get("package", {}))
    return package.get("autotests") is not False and not any(
        target.get("name") == "cli" for target in targets
    )


def check_workspace(root: Path) -> list[str]:
    errors: list[str] = []
    for directory in workspace_members(root):
        manifest_path = directory / "Cargo.toml"
        manifest = load_toml(manifest_path)
        if not has_binary(directory, manifest):
            continue

        tests = directory / "tests/cli.rs"
        if not tests.is_file() or not tests.read_bytes().strip():
            errors.append(f"{tests.relative_to(root)}: requires a nonempty CLI integration test")
        elif not cli_test_enabled(directory, manifest):
            errors.append(f"{tests.relative_to(root)}: must be enabled as a Cargo test target")
        dependencies = cast(dict[str, object], manifest.get("dev-dependencies", {}))
        if "cli-testing" not in dependencies:
            errors.append(
                f"{manifest_path.relative_to(root)}: requires cli-testing in [dev-dependencies]"
            )

        legacy = directory / "mk-outs.nu"
        if legacy.exists():
            errors.append(f"{legacy.relative_to(root)}: rename to mk-outputs.nu")
        generator = directory / "mk-outputs.nu"
        if generator.is_file() and not (directory / "tests/expected").is_dir():
            errors.append(f"{generator.relative_to(root)}: requires tests/expected/")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "root", type=Path, nargs="?", default=ROOT, help="repository workspace root"
    )
    args = parser.parse_args()
    try:
        errors = check_workspace(args.root.resolve())
    except (OSError, ValueError, KeyError) as error:
        print(f"Cannot check CLI test policy: {error}", file=sys.stderr)
        return 1
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("CLI test policy passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
