"""Selection regressions against real, temporary Git workspaces."""

from pathlib import Path

import pytest
from conftest import Repository
from select_suites import Suite


def test_suite_record_rejects_empty_path() -> None:
    with pytest.raises(ValueError):
        Suite(path="", package="tailr", directory="buildins/tailr", generate=False)


def test_whole_pr_uses_merge_base_and_includes_earlier_commits(repo: Repository) -> None:
    repo.git("checkout", "-b", "feature")
    repo.write("buildins/tailr/src/main.rs", "// changed\n")
    repo.commit()
    repo.write("README.md", "documentation in final PR commit\n")
    repo.commit()
    repo.git("checkout", "main")
    repo.write("buildins/echor/src/main.rs", "// target branch only\n")
    repo.base = repo.commit()
    repo.git("checkout", "feature")

    result = repo.selection()
    assert result["suites"] == ["buildins/tailr/benches/cli-bench.toml"]
    assert result["packages"] == ["biggie", "tailr"]
    assert result["binaries"] == ["tailr"]
    assert (result["runs"], result["warmup"]) == (1, 0)
    summary = (repo.root / "summary.md").read_text()
    assert "tailr" in summary and "1" in summary


def test_deleted_source_still_selects_owner(repo: Repository) -> None:
    repo.write("buildins/echor/src/module.rs", "// module\n")
    repo.base = repo.commit()
    (repo.root / "buildins/echor/src/module.rs").unlink()
    repo.commit()
    assert repo.selection()["packages"] == ["echor"]


@pytest.mark.parametrize(
    ("path", "content"),
    [
        ("buildins/echor/src/nested/module.rs", "pub fn run() {}\n"),
        ("buildins/echor/build.rs", "fn main() {}\n"),
    ],
)
def test_nested_source_and_build_script_select_owner(
    repo: Repository, path: str, content: str
) -> None:
    repo.write(path, content)
    repo.commit()
    assert repo.selection()["packages"] == ["echor"]


def test_suite_change_does_not_select_any_cli(repo: Repository) -> None:
    repo.write(
        "buildins/echor/benches/other.toml",
        'binary = "echor"\n[[cases]]\nname = "sample"\nargs = []\n',
    )
    repo.base = repo.commit()
    repo.write_suite("buildins/echor", "echor", generate=True)
    repo.commit()
    result = repo.selection()
    assert result["suites"] == []
    assert result["packages"] == []


def test_deleted_suite_is_not_scheduled(repo: Repository) -> None:
    (repo.root / "buildins/tailr/benches/cli-bench.toml").unlink()
    repo.commit()
    assert repo.selection()["suites"] == []


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "buildins/tailr/src/README.md",
        "buildins/tailr/src/input.txt",
        "buildins/tailr/examples/demo.rs",
        "buildins/tailr/README.md",
        "buildins/tailr/tests/cli.rs",
        "utils/cli-log/tests/basic.rs",
        "packages/biggie/benches/performance.rs",
        "tools/cli-bench/tests/test_bench.py",
        "tools/cli-bench/tests/test_select_suites.py",
        "tools/cli-bench/tests/conftest.py",
        "uv.lock",
        "pyproject.toml",
        "ruff.toml",
        "mypy.ini",
    ],
)
def test_docs_tests_and_python_tool_configuration_skip_native_suites(
    repo: Repository, path: str
) -> None:
    repo.write(path, "test or documentation change\n")
    repo.commit()
    result = repo.selection()
    assert result["suites"] == []
    assert result["packages"] == []


@pytest.mark.parametrize(
    "path",
    [
        "Cargo.toml",
        "rust-toolchain.toml",
        ".cargo/config.toml",
        "tools/cli-bench/bench.py",
        "tools/cli-bench/select_suites.py",
        "tools/cli-check/src/cli_check/workspace.py",
        ".github/workflows/cli-bench.yml",
        ".python-version",
    ],
)
def test_global_build_and_runner_changes_never_select_suites(repo: Repository, path: str) -> None:
    target = repo.root / path
    previous = target.read_text() if target.exists() else ""
    if path == "rust-toolchain.toml":
        previous = (Path(__file__).resolve().parents[3] / path).read_text()
    repo.write(path, previous + "\n# changed\n")
    repo.commit()
    assert repo.selection()["suites"] == []


@pytest.mark.parametrize(
    ("path", "content"),
    [
        ("packages/biggie/src/main.rs", "// changed\n"),
        ("packages/biggie/build.rs", "fn main() {}\n"),
    ],
)
def test_biggie_code_changes_select_only_biggie(repo: Repository, path: str, content: str) -> None:
    repo.write(path, content)
    repo.commit()
    result = repo.selection()
    assert result["suites"] == ["packages/biggie/benches/cli-bench.toml"]
    assert result["packages"] == ["biggie"]


@pytest.mark.parametrize(
    ("path", "content"),
    [
        ("utils/cli-log/src/lib.rs", "// shared logger\n"),
        ("utils/cli-log/build.rs", "fn main() {}\n"),
        ("utils/cli-log/Cargo.toml", '[package]\nname = "cli-log"\nversion = "0.1.1"\n'),
        ("utils/shared/src/lib.rs", "// shared helper\n"),
        ("utils/shared/build.rs", "fn main() {}\n"),
        ("utils/shared/Cargo.toml", '[package]\nname = "shared"\nversion = "0.1.1"\n'),
    ],
)
def test_shared_library_changes_never_select_consumers(
    repo: Repository, path: str, content: str
) -> None:
    repo.write("utils/shared/Cargo.toml", '[package]\nname = "shared"\n')
    repo.write("utils/shared/src/lib.rs", "// original helper\n")
    repo.lock(
        {
            "echor": ["shared"],
            "tailr": [],
            "wcr": [],
            "biggie": [],
            "shared": ["cli-log"],
            "cli-log": [],
        }
    )
    repo.base = repo.commit()
    repo.write(path, content)
    repo.commit()
    assert repo.selection()["binaries"] == []


def test_lockfile_dependency_additions_never_select_consumers(
    repo: Repository,
) -> None:
    repo.lock({"echor": ["shared"], "tailr": [], "wcr": [], "biggie": [], "shared": []})
    repo.write(
        "Cargo.lock",
        (repo.root / "Cargo.lock")
        .read_text()
        .replace(
            'name = "shared"\nversion = "0.1.0"',
            'name = "shared"\nversion = "0.1.0"\nsource = "registry+https://example.invalid/index"',
        ),
    )
    repo.base = repo.commit()
    before = (repo.root / "Cargo.lock").read_text()
    repo.write(
        "Cargo.lock",
        before.replace('["shared"]', '["shared 0.1.0"]').replace(
            'name = "tailr"\nversion = "0.1.0"\ndependencies = []',
            'name = "tailr"\nversion = "0.1.0"\ndependencies = ["shared 0.2.0"]',
        )
        + '\n[[package]]\nname = "shared"\nversion = "0.2.0"\nsource = "registry+https://example.invalid/index"\n',
    )
    repo.commit()
    assert repo.selection()["binaries"] == []


def test_lockfile_transitive_updates_never_select_consumers(
    repo: Repository,
) -> None:
    repo.lock(
        {"echor": [], "tailr": [], "wcr": [], "biggie": ["helper"], "helper": ["leaf"], "leaf": []}
    )
    repo.base = repo.commit()
    repo.write(
        "Cargo.lock",
        (repo.root / "Cargo.lock")
        .read_text()
        .replace('name = "leaf"\nversion = "0.1.0"', 'name = "leaf"\nversion = "0.2.0"'),
    )
    repo.commit()
    assert repo.selection()["binaries"] == []


def test_lockfile_comments_do_not_select_any_cli(repo: Repository) -> None:
    repo.write("Cargo.lock", (repo.root / "Cargo.lock").read_text() + "\n# comment\n")
    repo.commit()
    assert repo.selection()["binaries"] == []


@pytest.mark.parametrize(
    "body",
    [
        None,
        "",
        "Benchmark-CLIs: auto",
        "Benchmark-CLIs: all",
        "Benchmark-CLIs: tailr,wcr",
        "Benchmark-CLIs: invalid",
        "Benchmark-CLIs: tailr\nBenchmark-CLIs: wcr",
    ],
)
def test_pr_body_cannot_override_code_only_selection(repo: Repository, body: str | None) -> None:
    assert repo.selection(body)["binaries"] == []
    repo.write("buildins/echor/src/main.rs", "// changed\n")
    repo.commit()
    result = repo.selection(body)
    assert result["binaries"] == ["echor"]
    assert (result["runs"], result["warmup"]) == (1, 0)


def test_cli_manifest_changes_do_not_select_benchmarks(repo: Repository) -> None:
    repo.write("buildins/echor/Cargo.toml", '[package]\nname = "echor"\nversion = "0.1.1"\n')
    repo.commit()
    assert repo.selection()["suites"] == []


def test_source_rename_selects_both_owners(repo: Repository) -> None:
    repo.write("buildins/echor/src/module.rs", "// module\n")
    repo.base = repo.commit()
    repo.git("mv", "buildins/echor/src/module.rs", "buildins/wcr/src/moved.rs")
    repo.commit()
    assert repo.selection()["binaries"] == ["echor", "wcr"]


def test_unrelated_source_does_not_match_a_cli_name_prefix(repo: Repository) -> None:
    repo.write("buildins/echor-extra/src/main.rs", "fn main() {}\n")
    repo.write("buildins/echor-extra/Cargo.toml", '[package]\nname = "echor-extra"\n')
    repo.commit()
    assert repo.selection()["suites"] == []


def test_library_only_package_is_not_a_cli_even_with_a_suite(repo: Repository) -> None:
    repo.write_suite("utils/cli-log", "cli-log")
    repo.write("utils/cli-log/src/lib.rs", "// changed\n")
    repo.commit()
    assert repo.selection()["suites"] == []


@pytest.mark.parametrize("body", ["New description", "Benchmark-CLIs: all"])
def test_body_edits_never_rerun_benchmarks(repo: Repository, body: str) -> None:
    repo.write("buildins/echor/src/main.rs", "// changed\n")
    repo.commit()
    assert repo.selection(body, previous_body="Before")["suites"] == []


def test_suite_binary_must_match_owning_package_even_when_unselected(repo: Repository) -> None:
    repo.write_suite("buildins/echor", "other-binary")
    repo.commit()
    result = repo.run_selector()
    assert result.returncode != 0
    assert result.stdout == ""
    assert "other-binary" in result.stderr
    assert "echor" in result.stderr


@pytest.mark.parametrize(
    "case",
    [
        'name = "metadata"\nargs = []',
        'name = "sample"\nargs = []\nunknown = true',
        'name = "sample"\nargs = "bad"',
        'name = "sample"\nargs = []\n[[cases]]\nname = "sample"\nargs = []',
    ],
)
def test_selector_rejects_invalid_suite_even_when_unselected(repo: Repository, case: str) -> None:
    repo.write(
        "buildins/echor/benches/cli-bench.toml", 'binary = "echor"\n[[cases]]\n' + case + "\n"
    )
    repo.commit()
    result = repo.run_selector()
    assert result.returncode == 1
    assert result.stdout == ""
    assert "select_suites:" in result.stderr
