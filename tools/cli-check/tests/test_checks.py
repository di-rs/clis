"""Exercise the policy command against small, real Cargo workspace trees."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from cli_check.workspace import Binary, Package, discover


def test_workspace_records_validate_discovered_targets(tmp_path: Path) -> None:
    write(tmp_path, "Cargo.toml", '[workspace]\nmembers = ["buildins/*"]\n')
    add_cli(tmp_path)
    packages = discover(tmp_path)
    assert [package.name for package in packages] == ["echo", "cli-testing"]
    assert [binary.name for binary in packages[0].binaries] == ["echo"]
    assert packages[0].directory == (tmp_path / "buildins/echo").resolve()
    with pytest.raises(ValueError):
        Binary(name=123, source=tmp_path / "src/main.rs")
    with pytest.raises(ValueError):
        Package(
            name="echo",
            directory=tmp_path,
            manifest={},
            binaries=("invalid",),
            library=False,
        )


def write(root: Path, path: str, content: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


def add_cli(
    root: Path,
    directory: str = "buildins/echo",
    *,
    package_options: str = "",
    test_targets: str = "",
) -> None:
    write(root, "utils/cli-testing/Cargo.toml", '[package]\nname = "cli-testing"\n')
    write(root, "utils/cli-testing/src/lib.rs", "")
    helper = os.path.relpath(root / "utils/cli-testing", root / directory)
    write(
        root,
        f"{directory}/Cargo.toml",
        '[package]\nname = "echo"\nversion = "0.1.0"\nedition = "2024"\n'
        + package_options
        + f'[dev-dependencies]\ncli-testing = {{ path = "{helper}" }}\n'
        + test_targets,
    )
    write(root, f"{directory}/src/main.rs", "fn main() {}\n")
    write(root, f"{directory}/tests/cli.rs", "#[test]\nfn checks_cli() {}\n")


def run_policy(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cli_check", "tests", "--root", str(root)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_package_selection_ignores_unselected_violations_and_rejects_unknown(
    tmp_path: Path,
) -> None:
    write(tmp_path, "Cargo.toml", '[workspace]\nmembers = ["buildins/*"]\n')
    add_cli(tmp_path)
    write(tmp_path, "buildins/broken/Cargo.toml", '[package]\nname = "broken"\n')
    write(tmp_path, "buildins/broken/src/main.rs", "fn main() {}")
    command = [sys.executable, "-m", "cli_check", "tests", "--root", str(tmp_path), "-p"]
    result = subprocess.run(command + ["echo"], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    result = subprocess.run(command + ["missing"], capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert "Unknown packages: missing" in result.stderr


def test_valid_workspace_includes_globs_and_ignores_libraries_and_excludes(tmp_path: Path) -> None:
    write(
        tmp_path,
        "Cargo.toml",
        '[workspace]\nmembers = ["buildins/*", "utils/*"]\nexclude = ["buildins/excluded"]\n',
    )
    add_cli(tmp_path)
    write(tmp_path, "utils/helper/Cargo.toml", '[package]\nname = "helper"\n')
    write(tmp_path, "utils/helper/src/lib.rs", "pub fn helper() {}\n")
    write(tmp_path, "buildins/excluded/Cargo.toml", '[package]\nname = "excluded"\n')
    write(tmp_path, "buildins/excluded/src/main.rs", "fn main() {}\n")

    result = run_policy(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "CLI checks passed" in result.stdout


@pytest.mark.parametrize("contents", [None, "", " \n\t"])
def test_cli_requires_nonempty_e2e_entrypoint(tmp_path: Path, contents: str | None) -> None:
    write(tmp_path, "Cargo.toml", '[workspace]\nmembers = ["buildins/*"]\n')
    add_cli(tmp_path)
    entrypoint = tmp_path / "buildins/echo/tests/cli.rs"
    if contents is None:
        entrypoint.unlink()
    else:
        entrypoint.write_text(contents)

    result = run_policy(tmp_path)

    assert result.returncode == 1
    assert "buildins/echo/tests/cli.rs: requires a nonempty CLI integration test" in result.stderr


def test_new_cli_requires_shared_dev_dependency(tmp_path: Path) -> None:
    write(tmp_path, "Cargo.toml", '[workspace]\nmembers = ["buildins/*"]\n')
    add_cli(tmp_path)
    write(tmp_path, "buildins/echo/Cargo.toml", '[package]\nname = "echo"\n')

    result = run_policy(tmp_path)

    assert result.returncode == 1
    assert "buildins/echo/Cargo.toml: requires cli-testing in [dev-dependencies]" in result.stderr


def test_legacy_generator_is_rejected(tmp_path: Path) -> None:
    write(tmp_path, "Cargo.toml", '[workspace]\nmembers = ["buildins/*"]\n')
    add_cli(tmp_path)
    write(tmp_path, "buildins/echo/mk-outs.nu", "# legacy fixture generator\n")

    result = run_policy(tmp_path)

    assert result.returncode == 1
    assert "buildins/echo/mk-outs.nu: rename to mk-outputs.nu" in result.stderr


def test_generator_requires_expected_directory(tmp_path: Path) -> None:
    write(tmp_path, "Cargo.toml", '[workspace]\nmembers = ["buildins/*"]\n')
    add_cli(tmp_path)
    write(tmp_path, "buildins/echo/mk-outputs.nu", "# reference fixture generator\n")

    result = run_policy(tmp_path)

    assert result.returncode == 1
    assert "buildins/echo/mk-outputs.nu: requires tests/expected/" in result.stderr
    (tmp_path / "buildins/echo/tests/expected").mkdir()
    assert run_policy(tmp_path).returncode == 0


@pytest.mark.parametrize("binary", ['[[bin]]\nname = "echo"\npath = "app.rs"\n', ""])
def test_root_package_and_explicit_binary_targets_are_checked(tmp_path: Path, binary: str) -> None:
    write(tmp_path, "Cargo.toml", '[workspace]\nmembers = []\n[package]\nname = "echo"\n' + binary)
    write(tmp_path, "app.rs" if binary else "src/main.rs", "fn main() {}\n")

    result = run_policy(tmp_path)

    assert result.returncode == 1
    assert "tests/cli.rs: requires a nonempty CLI integration test" in result.stderr
    assert "Cargo.toml: requires cli-testing in [dev-dependencies]" in result.stderr


def test_disabled_binary_autodiscovery_does_not_require_cli_tests(tmp_path: Path) -> None:
    write(tmp_path, "Cargo.toml", '[workspace]\nmembers = ["helper"]\n')
    write(tmp_path, "helper/Cargo.toml", '[package]\nname = "helper"\nautobins = false\n')
    write(tmp_path, "helper/src/main.rs", "// not a Cargo binary target\n")
    write(tmp_path, "helper/src/lib.rs", "pub fn helper() {}\n")

    result = run_policy(tmp_path)

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("package_options", "test_targets", "enabled"),
    [
        pytest.param("autotests = false\n", "", False, id="autodiscovery-disabled"),
        pytest.param(
            "autotests = false\n", '[[test]]\nname = "cli"\n', True, id="explicit-default-path"
        ),
        pytest.param(
            "autotests = false\n",
            '[[test]]\nname = "end_to_end"\npath = "tests/cli.rs"\n',
            True,
            id="explicit-renamed-target",
        ),
        pytest.param(
            "autotests = false\n",
            '[[test]]\nname = "cli"\ntest = false\n',
            False,
            id="explicit-disabled-target",
        ),
        pytest.param(
            "",
            '[[test]]\nname = "cli"\ntest = false\n',
            False,
            id="disabled-target-overrides-autodiscovery",
        ),
        pytest.param(
            "",
            '[[test]]\nname = "end_to_end"\npath = "tests/cli.rs"\ntest = false\n',
            False,
            id="disabled-path-overrides-autodiscovery",
        ),
        pytest.param(
            "",
            '[[test]]\nname = "cli"\npath = "tests/other.rs"\n',
            False,
            id="redirected-target-overrides-autodiscovery",
        ),
        pytest.param(
            "",
            '[[test]]\nname = "cli"\npath = "tests/other.rs"\n'
            '[[test]]\nname = "end_to_end"\npath = "./tests/cli.rs"\n',
            True,
            id="enabled-entrypoint-alongside-redirected-target",
        ),
        pytest.param(
            "",
            '[[test]]\nname = "other"\ntest = false\n',
            True,
            id="unrelated-disabled-target-keeps-autodiscovery",
        ),
    ],
)
def test_cli_entrypoint_must_be_enabled_as_a_test_target(
    tmp_path: Path, package_options: str, test_targets: str, enabled: bool
) -> None:
    write(tmp_path, "Cargo.toml", '[workspace]\nmembers = ["buildins/*"]\n')
    add_cli(tmp_path, package_options=package_options, test_targets=test_targets)
    write(tmp_path, "buildins/echo/tests/other.rs", "#[test]\nfn other() {}\n")

    result = run_policy(tmp_path)

    assert result.returncode == (0 if enabled else 1), result.stderr
    if not enabled:
        assert "buildins/echo/tests/cli.rs: must be enabled as a Cargo test target" in result.stderr


def run_check(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cli_check", "--root", str(root), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    write(tmp_path, "Cargo.toml", '[workspace]\nmembers = ["buildins/*", "utils/*"]\n')
    add_cli(tmp_path)
    write(tmp_path, "README.md", "[echo](buildins/echo/README.md)\n")
    write(tmp_path, "buildins/echo/README.md", "# echo\nPrint arguments.\n")
    write(
        tmp_path,
        "buildins/echo/benches/cli-bench.toml",
        'binary = "echo"\n[[cases]]\nname = "basic"\nargs = []\n',
    )
    return tmp_path


def test_all_groups_accept_valid_workspace(workspace: Path) -> None:
    result = run_check(workspace)
    assert result.returncode == 0, result.stderr
    assert "CLI checks passed" in result.stdout


@pytest.mark.parametrize(
    ("path", "content", "message"),
    [
        ("src/bin/extra.rs", "fn main() {}", "exactly one binary"),
        ("Cargo.toml", '[package]\nname = "different"\n', "must match directory"),
        (
            "Cargo.toml",
            '[package]\nname = "echo"\n[[bin]]\nname = "other"\npath = "src/main.rs"\n',
            "must match package",
        ),
        (
            "Cargo.toml",
            '[package]\nname = "echo"\nautobins = false\n[[bin]]\nname = "echo"\npath = "app.rs"\n',
            "src/main.rs",
        ),
    ],
)
def test_layout_reports_broken_binary_boundaries(
    workspace: Path, path: str, content: str, message: str
) -> None:
    write(workspace, f"buildins/echo/{path}", content)
    result = run_check(workspace, "layout")
    assert result.returncode == 1
    assert message in result.stderr
    assert "layout:" in result.stderr


def test_bin_only_package_is_not_hidden(workspace: Path) -> None:
    (workspace / "buildins/echo/src/main.rs").unlink()
    write(workspace, "buildins/echo/src/bin/echo.rs", "fn main() {}\n")
    result = run_check(workspace, "layout")
    assert result.returncode == 1
    assert "src/main.rs" in result.stderr


def test_explicit_matching_binary_does_not_duplicate_automatic_target(workspace: Path) -> None:
    manifest = workspace / "buildins/echo/Cargo.toml"
    manifest.write_text(manifest.read_text() + '\n[[bin]]\nname = "echo"\npath = "src/main.rs"\n')
    assert run_check(workspace, "layout").returncode == 0


def test_library_and_cli_library_are_allowed(workspace: Path) -> None:
    write(workspace, "utils/helper/Cargo.toml", '[package]\nname = "helper"\n')
    write(workspace, "utils/helper/src/lib.rs", "pub fn helper() {}\n")
    write(workspace, "buildins/echo/src/lib.rs", "pub fn helper() {}\n")
    assert run_check(workspace).returncode == 0


def test_groups_do_not_run_other_checks(workspace: Path) -> None:
    (workspace / "buildins/echo/tests/cli.rs").unlink()
    assert run_check(workspace, "layout").returncode == 0
    assert run_check(workspace, "tests").returncode == 1


def test_root_can_be_found_from_package_cwd(workspace: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "cli_check", "layout"],
        cwd=workspace / "buildins/echo",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_invalid_group_and_missing_workspace_have_distinct_exit_codes(tmp_path: Path) -> None:
    assert run_check(tmp_path, "unknown").returncode == 2
    result = run_check(tmp_path)
    assert result.returncode == 1
    assert "Traceback" not in result.stderr


def test_multiple_violations_are_reported_in_path_order(workspace: Path) -> None:
    (workspace / "buildins/echo/tests/cli.rs").unlink()
    write(workspace, "buildins/echo/mk-outs.nu", "# legacy\n")
    result = run_check(workspace, "tests")
    assert result.returncode == 1
    assert "mk-outs.nu" in result.stderr and "tests/cli.rs" in result.stderr
    assert result.stderr.index("mk-outs.nu") < result.stderr.index("tests/cli.rs")


@pytest.mark.parametrize("contents", [None, "", " \n"])
def test_docs_require_nonempty_package_readme(workspace: Path, contents: str | None) -> None:
    readme = workspace / "buildins/echo/README.md"
    if contents is None:
        readme.unlink()
    else:
        readme.write_text(contents)
    result = run_check(workspace, "docs")
    assert result.returncode == 1
    assert "buildins/echo/README.md" in result.stderr


def test_root_readme_must_link_each_cli(workspace: Path) -> None:
    write(workspace, "README.md", "# Tools\n")
    result = run_check(workspace, "docs")
    assert result.returncode == 1
    assert "root README" in result.stderr


def test_docs_leave_general_links_to_lychee(workspace: Path) -> None:
    write(workspace, "buildins/echo/a b.md", "# Details\n")
    write(workspace, "buildins/echo/logo.svg", "<svg/>\n")
    write(
        workspace,
        "buildins/echo/README.md",
        "# echo\n[details][guide]\n\n[guide]: a%20b.md#details\n\n"
        "![logo](logo.svg)\n[web](https://example.invalid)\n[section](#echo)\n"
        "```md\n[ignored](missing.md)\n```\n",
    )
    assert run_check(workspace, "docs").returncode == 0
    (workspace / "buildins/echo/a b.md").unlink()
    (workspace / "buildins/echo/logo.svg").unlink()
    result = run_check(workspace, "docs")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("suite", "message"),
    [
        (None, "benchmark suite"),
        ('binary = "other"\n[[cases]]\nname = "a"\nargs = []\n', "owning package"),
        ('binary = "echo"\n', "at least one case"),
        ('binary = "echo"\n[[cases]]\nname = ""\nargs = []\n', "case name"),
        ('binary = "echo"\n[[cases]]\nname = "a"\nargs = "bad"\n', "array of strings"),
        (
            'binary = "echo"\n[[cases]]\nname = "a"\nargs = []\n[[cases]]\nname = "a"\nargs = []\n',
            "duplicate case name",
        ),
        ("binary = [", "suite-data"),
    ],
)
def test_benchmarks_report_missing_or_malformed_suites(
    workspace: Path, suite: str | None, message: str
) -> None:
    path = workspace / "buildins/echo/benches/cli-bench.toml"
    if suite is None:
        path.unlink()
    else:
        path.write_text(suite)
    result = run_check(workspace, "benches")
    assert result.returncode == 1
    assert message in result.stderr
    assert "Traceback" not in result.stderr


def test_benchmark_exemption_needs_reason_and_no_suite(workspace: Path) -> None:
    manifest = workspace / "buildins/echo/Cargo.toml"
    original = manifest.read_text()
    manifest.write_text(original + '\n[package.metadata.cli-check]\nbenchmark-exemption = ""\n')
    assert run_check(workspace, "benches").returncode == 1
    manifest.write_text(
        original + '\n[package.metadata.cli-check]\nbenchmark-exemption = "Interactive tool"\n'
    )
    result = run_check(workspace, "benches")
    assert result.returncode == 1
    assert "exemption" in result.stderr
    (workspace / "buildins/echo/benches/cli-bench.toml").unlink()
    assert run_check(workspace, "benches").returncode == 0


def test_invalid_manifest_data_is_actionable(tmp_path: Path) -> None:
    write(tmp_path, "Cargo.toml", '[workspace]\nmembers = "oops"\n')
    result = run_check(tmp_path)
    assert result.returncode == 1
    assert "members" in result.stderr and "Traceback" not in result.stderr


def test_root_cli_readme_is_already_indexed(tmp_path: Path) -> None:
    root = tmp_path / "echo"
    add_cli(root, directory=".")
    manifest = root / "Cargo.toml"
    manifest.write_text(manifest.read_text() + "\n[workspace]\nmembers = []\n")
    write(root, "README.md", "# echo\nPrint arguments.\n")
    write(root, "benches/basic.toml", 'binary = "echo"\n[[cases]]\nname = "basic"\nargs = []\n')
    result = run_check(root)
    assert result.returncode == 0, result.stderr


def test_implicit_workspace_member_is_checked(tmp_path: Path) -> None:
    write(tmp_path, "Cargo.toml", '[workspace]\nmembers = ["app"]\n')
    write(
        tmp_path,
        "app/Cargo.toml",
        '[package]\nname = "app"\n[dependencies]\nhelper = { path = "../helper" }\n',
    )
    write(tmp_path, "app/src/main.rs", "fn main() {}\n")
    write(tmp_path, "helper/Cargo.toml", '[package]\nname = "helper"\n')
    write(tmp_path, "helper/src/lib.rs", "")
    write(tmp_path, "helper/src/bin/extra.rs", "fn main() {}\n")
    result = run_check(tmp_path, "layout")
    assert result.returncode == 1
    assert "helper/Cargo.toml" in result.stderr
    assert "src/main.rs" in result.stderr


def test_invalid_cargo_target_reports_cargo_error(tmp_path: Path) -> None:
    write(tmp_path, "Cargo.toml", '[workspace]\nmembers = ["broken"]\n')
    write(tmp_path, "broken/Cargo.toml", '[package]\nname = "broken"\n')
    result = run_check(tmp_path)
    assert result.returncode == 1
    assert "cargo metadata" in result.stderr
    assert "no targets" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    "case",
    [
        'name = "metadata"\nargs = []',
        'name = "valid"\nargs = []\nunknown = true',
        'name = "valid"\nargs = []\nexpected_exit = 256',
    ],
)
def test_benchmarks_reject_runner_invalid_cases(workspace: Path, case: str) -> None:
    write(
        workspace,
        "buildins/echo/benches/cli-bench.toml",
        'binary = "echo"\n[[cases]]\n' + case + "\n",
    )
    result = run_check(workspace, "benches")
    assert result.returncode == 1
    assert "suite-data" in result.stderr


def test_missing_cargo_is_actionable(tmp_path: Path) -> None:
    write(tmp_path, "Cargo.toml", "[workspace]\nmembers = []\n")
    result = subprocess.run(
        [sys.executable, "-m", "cli_check", "--root", str(tmp_path)],
        env=os.environ | {"PATH": str(tmp_path)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert "Cannot run cargo metadata" in result.stderr
    assert "Traceback" not in result.stderr


def test_root_index_resolves_reference_links_and_encoded_paths(workspace: Path) -> None:
    write(workspace, "README.md", "[echo][cli]\n\n[cli]: buildins/%65cho/README.md#echo\n")
    result = run_check(workspace, "docs")
    assert result.returncode == 0, result.stderr
    assert not (workspace / "Cargo.lock").exists()
