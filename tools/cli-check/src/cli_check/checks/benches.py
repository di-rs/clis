"""Check suite completeness and ownership without executing benchmark commands."""

from pathlib import Path

from cli_check.diagnostics import Diagnostic
from cli_check.workspace import Package, load_toml, table


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
                suite = load_toml(path)
                if suite.get("binary") != package.name:
                    errors.append(
                        Diagnostic(
                            relative,
                            "benches",
                            "owner",
                            f"suite binary must match owning package {package.name!r}",
                        )
                    )
                cases = suite.get("cases")
                if not isinstance(cases, list) or not cases:
                    errors.append(
                        Diagnostic(relative, "benches", "cases", "suite requires at least one case")
                    )
                    continue
                names: set[str] = set()
                for raw in cases:
                    case = table(raw)
                    name = case.get("name")
                    if not isinstance(name, str) or not name.strip():
                        errors.append(
                            Diagnostic(
                                relative, "benches", "case-name", "requires a nonempty case name"
                            )
                        )
                    elif name.casefold() in names:
                        errors.append(
                            Diagnostic(
                                relative, "benches", "case-name", "case names must be unique"
                            )
                        )
                    else:
                        names.add(name.casefold())
                    arguments = case.get("args")
                    if not isinstance(arguments, list) or not all(
                        isinstance(value, str) for value in arguments
                    ):
                        errors.append(
                            Diagnostic(
                                relative,
                                "benches",
                                "case-args",
                                "case requires an argument array of strings",
                            )
                        )
            except (OSError, ValueError) as error:
                errors.append(Diagnostic(relative, "benches", "suite-data", str(error)))
    return errors
