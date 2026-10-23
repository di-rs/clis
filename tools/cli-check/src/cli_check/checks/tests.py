"""Check test-target registration and fixture layout, without parsing Rust."""

from pathlib import Path

from cli_check.diagnostics import Diagnostic
from cli_check.workspace import Package, table, tables


def cli_test_enabled(package: Package) -> bool:
    return (package.directory / "tests/cli.rs").resolve() in package.enabled_tests


def check(root: Path, packages: list[Package]) -> list[Diagnostic]:
    errors: list[Diagnostic] = []
    for package in packages:
        if not package.is_cli:
            continue

        def error(path: str, rule: str, message: str, directory: Path = package.directory) -> None:
            errors.append(
                Diagnostic((directory / path).relative_to(root).as_posix(), "tests", rule, message)
            )

        tests = package.directory / "tests/cli.rs"
        if not tests.is_file() or not tests.read_bytes().strip():
            error("tests/cli.rs", "entrypoint", "requires a nonempty CLI integration test")
        elif not cli_test_enabled(package):
            error("tests/cli.rs", "enabled-target", "must be enabled as a Cargo test target")
        if "cli-testing" not in table(package.manifest.get("dev-dependencies", {})):
            error("Cargo.toml", "helper", "requires cli-testing in [dev-dependencies]")
        catalog = package.directory / "tests/cases.toml"
        runner = package.directory / "tests/catalog.rs"
        if catalog.exists() or runner.exists():
            if not catalog.is_file() or not catalog.read_bytes().strip():
                error("tests/cases.toml", "catalog", "requires a nonempty catalog")
            if not runner.is_file() or not runner.read_bytes().strip():
                error(
                    "tests/catalog.rs", "catalog-entrypoint", "requires a nonempty catalog runner"
                )
            elif runner.resolve() not in package.enabled_tests:
                error(
                    "tests/catalog.rs", "catalog-target", "must be enabled as a Cargo test target"
                )
            registered = any(
                target.get("name") == "catalog"
                and target.get("path", "tests/catalog.rs") == "tests/catalog.rs"
                and target.get("harness") is False
                for target in tables(package.manifest.get("test", []))
            )
            if not registered:
                error("Cargo.toml", "catalog-harness", "register catalog with harness = false")
        if (package.directory / "mk-outs.nu").exists():
            error("mk-outs.nu", "generator-name", "rename to mk-outputs.nu")
        if (package.directory / "mk-outputs.nu").is_file() and not (
            package.directory / "tests/expected"
        ).is_dir():
            error("mk-outputs.nu", "expected-fixtures", "requires tests/expected/")
    return errors
