"""Check test-target registration and fixture layout, without parsing Rust."""

from pathlib import Path

from cli_check.diagnostics import Diagnostic
from cli_check.workspace import Package, string, table, tables


def cli_test_enabled(package: Package) -> bool:
    targets = tables(package.manifest.get("test", []))
    entrypoint = (package.directory / "tests/cli.rs").resolve()
    explicit = [
        target
        for target in targets
        if (
            package.directory
            / string(target.get("path", f"tests/{target['name']}.rs"), "test.path")
        ).resolve()
        == entrypoint
    ]
    if explicit:
        return any(target.get("test") is not False for target in explicit)
    settings = table(package.manifest["package"])
    return settings.get("autotests") is not False and not any(
        target.get("name") == "cli" for target in targets
    )


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
        if (package.directory / "mk-outs.nu").exists():
            error("mk-outs.nu", "generator-name", "rename to mk-outputs.nu")
        if (package.directory / "mk-outputs.nu").is_file() and not (
            package.directory / "tests/expected"
        ).is_dir():
            error("mk-outputs.nu", "expected-fixtures", "requires tests/expected/")
    return errors
