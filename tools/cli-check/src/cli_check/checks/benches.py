"""Check suite completeness and ownership without executing benchmark commands."""

from pathlib import Path

from cli_check.benchmark import load_suite
from cli_check.diagnostics import Diagnostic
from cli_check.workspace import Package, table


def check(root: Path, packages: list[Package]) -> list[Diagnostic]:
    errors: list[Diagnostic] = []
    for package in packages:
        if not package.is_cli:
            continue
        suites = sorted(package.directory.glob("benches/*.toml"))
        metadata = table(table(package.manifest["package"]).get("metadata", {}))
        settings = table(metadata.get("cli-check", {}))
        exemption = settings.get("benchmark-exemption")
        manifest = (package.directory / "Cargo.toml").relative_to(root).as_posix()
        if exemption is not None:
            if not isinstance(exemption, str) or not exemption.strip():
                errors.append(
                    Diagnostic(
                        manifest,
                        "benches",
                        "exemption-reason",
                        "benchmark exemption requires a nonempty reason",
                    )
                )
            if suites:
                errors.append(
                    Diagnostic(
                        manifest,
                        "benches",
                        "obsolete-exemption",
                        "remove benchmark exemption when a suite exists",
                    )
                )
        elif not suites:
            errors.append(
                Diagnostic(
                    manifest,
                    "benches",
                    "suite",
                    "requires a benchmark suite under benches/ or an explicit exemption",
                )
            )
        for path in suites:
            relative = path.relative_to(root).as_posix()
            try:
                suite = load_suite(path)
                if suite.get("binary") != package.name:
                    errors.append(
                        Diagnostic(
                            relative,
                            "benches",
                            "owner",
                            f"suite binary must match owning package {package.name!r}",
                        )
                    )
            except (OSError, ValueError) as error:
                errors.append(Diagnostic(relative, "benches", "suite-data", str(error)))
    return errors
