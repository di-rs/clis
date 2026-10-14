"""Exercise the policy command against small, real Cargo workspace trees."""

import subprocess
import sys
from pathlib import Path

import pytest

CHECKER = Path(__file__).resolve().parents[1] / "cli_test_policy.py"


def write(root: Path, path: str, content: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


def add_cli(
    root: Path,
    directory: str = "coreutils/echo",
    *,
    package_options: str = "",
    test_targets: str = "",
) -> None:
    write(
        root,
        f"{directory}/Cargo.toml",
        '[package]\nname = "echo"\nversion = "0.1.0"\nedition = "2024"\n'
        + package_options
        + '[dev-dependencies]\ncli-testing = { path = "../../utils/cli-testing" }\n'
        + test_targets,
    )
    write(root, f"{directory}/src/main.rs", "fn main() {}\n")
    write(root, f"{directory}/tests/cli.rs", "#[test]\nfn checks_cli() {}\n")


def run_policy(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), str(root)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_valid_workspace_includes_globs_and_ignores_libraries_and_excludes(tmp_path: Path) -> None:
    write(
        tmp_path,
        "Cargo.toml",
        '[workspace]\nmembers = ["coreutils/*", "utils/*"]\nexclude = ["coreutils/excluded"]\n',
    )
    add_cli(tmp_path)
    write(tmp_path, "utils/helper/Cargo.toml", '[package]\nname = "helper"\n')
    write(tmp_path, "utils/helper/src/lib.rs", "pub fn helper() {}\n")
    write(tmp_path, "coreutils/excluded/Cargo.toml", '[package]\nname = "excluded"\n')
    write(tmp_path, "coreutils/excluded/src/main.rs", "fn main() {}\n")

    result = run_policy(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "CLI test policy passed" in result.stdout


@pytest.mark.parametrize("contents", [None, "", " \n\t"])
def test_cli_requires_nonempty_e2e_entrypoint(tmp_path: Path, contents: str | None) -> None:
    write(tmp_path, "Cargo.toml", '[workspace]\nmembers = ["coreutils/*"]\n')
    add_cli(tmp_path)
    entrypoint = tmp_path / "coreutils/echo/tests/cli.rs"
    if contents is None:
        entrypoint.unlink()
    else:
        entrypoint.write_text(contents)

    result = run_policy(tmp_path)

    assert result.returncode == 1
    assert "coreutils/echo/tests/cli.rs: requires a nonempty CLI integration test" in result.stderr


def test_new_cli_requires_shared_dev_dependency(tmp_path: Path) -> None:
    write(tmp_path, "Cargo.toml", '[workspace]\nmembers = ["coreutils/*"]\n')
    add_cli(tmp_path)
    write(tmp_path, "coreutils/echo/Cargo.toml", '[package]\nname = "echo"\n')

    result = run_policy(tmp_path)

    assert result.returncode == 1
    assert "coreutils/echo/Cargo.toml: requires cli-testing in [dev-dependencies]" in result.stderr


def test_legacy_generator_is_rejected(tmp_path: Path) -> None:
    write(tmp_path, "Cargo.toml", '[workspace]\nmembers = ["coreutils/*"]\n')
    add_cli(tmp_path)
    write(tmp_path, "coreutils/echo/mk-outs.nu", "# legacy fixture generator\n")

    result = run_policy(tmp_path)

    assert result.returncode == 1
    assert "coreutils/echo/mk-outs.nu: rename to mk-outputs.nu" in result.stderr


def test_generator_requires_expected_directory(tmp_path: Path) -> None:
    write(tmp_path, "Cargo.toml", '[workspace]\nmembers = ["coreutils/*"]\n')
    add_cli(tmp_path)
    write(tmp_path, "coreutils/echo/mk-outputs.nu", "# reference fixture generator\n")

    result = run_policy(tmp_path)

    assert result.returncode == 1
    assert "coreutils/echo/mk-outputs.nu: requires tests/expected/" in result.stderr
    (tmp_path / "coreutils/echo/tests/expected").mkdir()
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
    write(tmp_path, "Cargo.toml", '[workspace]\nmembers = ["coreutils/*"]\n')
    add_cli(tmp_path, package_options=package_options, test_targets=test_targets)
    write(tmp_path, "coreutils/echo/tests/other.rs", "#[test]\nfn other() {}\n")

    result = run_policy(tmp_path)

    assert result.returncode == (0 if enabled else 1), result.stderr
    if not enabled:
        assert (
            "coreutils/echo/tests/cli.rs: must be enabled as a Cargo test target" in result.stderr
        )
