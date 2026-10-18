"""Selection regressions against real, temporary Git workspaces."""

from pathlib import Path

import pytest
from cli_bench.select_suites import Suite
from conftest import Repository

ALL_SUITES = [
    "biggie/benches/cli-bench.toml",
    "coreutils/echor/benches/cli-bench.toml",
    "coreutils/tailr/benches/cli-bench.toml",
    "coreutils/wcr/benches/cli-bench.toml",
]


def test_suite_record_rejects_empty_path() -> None:
    with pytest.raises(ValueError):
        Suite(path="", package="tailr", directory="coreutils/tailr", generate=False)


def test_moved_shared_library_selects_dependents(repo: Repository) -> None:
    repo.git("mv", "utils/cli-log", "shared-log")
    repo.write(
        "Cargo.toml",
        '[workspace]\nmembers = ["biggie", "coreutils/*", "shared-log"]\nexclude = ["coreutils/excluded"]\n',
    )
    repo.write(
        "coreutils/echor/Cargo.toml",
        '[package]\nname = "echor"\n[dependencies]\ncli-log = { path = "../../shared-log" }\n',
    )
    repo.base = repo.commit()
    repo.write("shared-log/src/lib.rs", "pub fn log() {}\n")
    repo.commit()
    assert repo.selection()["binaries"] == ["echor"]


def test_moved_generator_selects_generated_suites(repo: Repository) -> None:
    repo.git("mv", "biggie", "generator")
    repo.write(
        "Cargo.toml",
        '[workspace]\nmembers = ["generator", "coreutils/*", "utils/*"]\nexclude = ["coreutils/excluded"]\n',
    )
    repo.base = repo.commit()
    repo.write("generator/src/main.rs", 'fn main() { println!("changed"); }\n')
    repo.commit()
    assert repo.selection()["binaries"] == ["biggie", "tailr"]


def test_whole_pr_uses_merge_base_and_includes_earlier_commits(repo: Repository) -> None:
    repo.git("checkout", "-b", "feature")
    repo.write("coreutils/tailr/src/main.rs", "// changed\n")
    repo.commit()
    repo.write("README.md", "documentation in final PR commit\n")
    repo.commit()
    repo.git("checkout", "main")
    repo.write("coreutils/echor/src/main.rs", "// target branch only\n")
    repo.base = repo.commit()
    repo.git("checkout", "feature")

    result = repo.selection()
    assert result["suites"] == ["coreutils/tailr/benches/cli-bench.toml"]
    assert result["packages"] == ["biggie", "tailr"]
    assert result["binaries"] == ["tailr"]
    assert (result["runs"], result["warmup"]) == (1, 0)
    summary = (repo.root / "summary.md").read_text()
    assert "tailr" in summary and "1" in summary


def test_deleted_source_still_selects_owner(repo: Repository) -> None:
    repo.write("coreutils/echor/src/extra.rs", "// extra source\n")
    repo.base = repo.commit()
    (repo.root / "coreutils/echor/src/extra.rs").unlink()
    repo.commit()
    assert repo.selection()["packages"] == ["echor"]


@pytest.mark.parametrize(
    ("path", "content"),
    [
        ("coreutils/echor/Cargo.toml", '[package]\nname = "echor"\nversion = "0.1.1"\n'),
        ("coreutils/echor/build.rs", "fn main() {}\n"),
    ],
)
def test_package_manifest_and_build_script_select_owner(
    repo: Repository, path: str, content: str
) -> None:
    repo.write(path, content)
    repo.commit()
    assert repo.selection()["packages"] == ["echor"]


def test_suite_change_selects_only_that_suite(repo: Repository) -> None:
    repo.write("coreutils/echor/benches/other.toml", 'binary = "echor"\n')
    repo.base = repo.commit()
    repo.write_suite("coreutils/echor", "echor", generate=True)
    repo.commit()
    result = repo.selection()
    assert result["suites"] == ["coreutils/echor/benches/cli-bench.toml"]
    assert result["packages"] == ["biggie", "echor"]


def test_deleted_suite_is_not_scheduled(repo: Repository) -> None:
    (repo.root / "coreutils/tailr/benches/cli-bench.toml").unlink()
    repo.commit()
    assert repo.selection()["suites"] == []


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "coreutils/tailr/README.md",
        "coreutils/tailr/tests/cli.rs",
        "utils/cli-log/tests/basic.rs",
        "biggie/benches/performance.rs",
        "tools/cli-bench/tests/test_bench.py",
        "tools/cli-bench/tests/test_select_suites.py",
        "tools/cli-bench/tests/conftest.py",
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
        "Cargo.lock",
        "rust-toolchain.toml",
        ".cargo/config.toml",
        "tools/cli-bench/src/cli_bench/bench.py",
        "tools/cli-bench/src/cli_bench/select_suites.py",
        "tools/cli-check/src/cli_check/workspace.py",
        ".github/workflows/cli-bench.yml",
        ".python-version",
    ],
)
def test_global_build_and_runner_changes_select_all_workspace_suites(
    repo: Repository, path: str
) -> None:
    target = repo.root / path
    previous = (
        target.read_text()
        if target.exists()
        else (Path("rust-toolchain.toml").read_text() if path == "rust-toolchain.toml" else "")
    )
    repo.write(path, previous + "\n# changed\n")
    repo.commit()
    assert repo.selection()["suites"] == ALL_SUITES


@pytest.mark.parametrize(
    ("path", "content"),
    [
        ("biggie/src/main.rs", "// changed\n"),
        ("biggie/build.rs", "fn main() {}\n"),
        ("biggie/Cargo.toml", '[package]\nname = "biggie"\nversion = "0.1.1"\n'),
    ],
)
def test_biggie_production_changes_select_biggie_and_generated_suites(
    repo: Repository, path: str, content: str
) -> None:
    repo.write(path, content)
    repo.commit()
    result = repo.selection()
    assert result["suites"] == [
        "biggie/benches/cli-bench.toml",
        "coreutils/tailr/benches/cli-bench.toml",
    ]
    assert result["packages"] == ["biggie", "tailr"]


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
def test_shared_logger_and_other_utility_production_changes_select_all(
    repo: Repository, path: str, content: str
) -> None:
    repo.write("utils/shared/Cargo.toml", '[package]\nname = "shared"\n')
    repo.write("utils/shared/src/lib.rs", "// shared library\n")
    for name in ("biggie", "echor", "tailr", "wcr"):
        directory = "biggie" if name == "biggie" else f"coreutils/{name}"
        prefix = "../" if name == "biggie" else "../../"
        repo.write(
            f"{directory}/Cargo.toml",
            f'[package]\nname = "{name}"\n[dependencies]\nshared = {{path = "{prefix}utils/shared"}}\ncli-log = {{path = "{prefix}utils/cli-log"}}\n',
        )
    repo.base = repo.commit()
    repo.write(path, content)
    repo.commit()
    assert repo.selection()["suites"] == ALL_SUITES


@pytest.mark.parametrize(
    "body", [None, "", "Some description", "Benchmark-CLIs:", "Benchmark-CLIs: auto"]
)
def test_empty_and_auto_requests_use_affected_smoke_selection(
    repo: Repository, body: str | None
) -> None:
    repo.write("coreutils/echor/src/main.rs", "// changed\n")
    repo.commit()
    result = repo.selection(body)
    assert result["packages"] == ["echor"]
    assert (result["runs"], result["warmup"]) == (1, 0)


def test_explicit_request_overrides_affected_suites_and_uses_full_runs(repo: Repository) -> None:
    repo.write("coreutils/echor/src/main.rs", "// changed\n")
    repo.commit()
    result = repo.selection("Description\n\nBenchmark-CLIs: tailr, wcr\n\nMore details")
    assert result["suites"] == [
        "coreutils/tailr/benches/cli-bench.toml",
        "coreutils/wcr/benches/cli-bench.toml",
    ]
    assert result["packages"] == ["biggie", "tailr", "wcr"]
    assert (result["runs"], result["warmup"]) == (20, 3)


def test_all_request_uses_full_runs_even_without_code_changes(repo: Repository) -> None:
    result = repo.selection("Benchmark-CLIs: all")
    assert result["suites"] == ALL_SUITES
    assert (result["runs"], result["warmup"]) == (20, 3)


def test_request_must_be_a_standalone_field(repo: Repository) -> None:
    result = repo.selection("Please use Benchmark-CLIs: tailr\n> Benchmark-CLIs: wcr")
    assert result["suites"] == []


@pytest.mark.parametrize(
    ("previous", "current"),
    [
        ("Before", "After"),
        ("Before", "After\nBenchmark-CLIs: auto"),
        ("Benchmark-CLIs: tailr,wcr", "After\nBenchmark-CLIs: wcr, tailr"),
    ],
)
def test_unrelated_body_edit_does_not_repeat_previous_selection(
    repo: Repository, previous: str, current: str
) -> None:
    repo.write("coreutils/echor/src/main.rs", "// changed\n")
    repo.commit()
    result = repo.selection(current, previous_body=previous)
    assert result["suites"] == []
    assert result["packages"] == []
    assert result["binaries"] == []
    assert (result["runs"], result["warmup"]) == (1, 0)


@pytest.mark.parametrize(
    "previous",
    [
        "Benchmark-CLIs: tailr",
        "Benchmark-CLIs: invalid",
        "Benchmark-CLIs: tailr\nBenchmark-CLIs: tailr",
    ],
)
def test_body_edit_changes_request_or_repairs_invalid_request(
    repo: Repository, previous: str
) -> None:
    result = repo.selection("Benchmark-CLIs: wcr", previous_body=previous)
    assert result["binaries"] == ["wcr"]
    assert result["packages"] == ["wcr"]
    assert (result["runs"], result["warmup"]) == (20, 3)


@pytest.mark.parametrize(
    ("body", "diagnostic"),
    [
        ("Benchmark-CLIs: missing", "missing"),
        ("Benchmark-CLIs: excluded", "excluded"),
        ("Benchmark-CLIs: tailr,", "Benchmark-CLIs"),
        ("Benchmark-CLIs: all,tailr", "all"),
        ("Benchmark-CLIs: tailr\nBenchmark-CLIs: wcr", "duplicate"),
    ],
)
def test_invalid_and_duplicate_fields_fail_without_selection(
    repo: Repository, body: str, diagnostic: str
) -> None:
    result = repo.run_selector(body)
    assert result.returncode != 0
    assert result.stdout == ""
    assert diagnostic in result.stderr


def test_suite_binary_must_match_owning_package_even_when_unselected(repo: Repository) -> None:
    repo.write_suite("coreutils/echor", "other-binary")
    repo.commit()
    result = repo.run_selector("Benchmark-CLIs: wcr")
    assert result.returncode != 0
    assert result.stdout == ""
    assert "other-binary" in result.stderr
    assert "echor" in result.stderr


@pytest.mark.parametrize(
    "table", ["dependencies", "build-dependencies", "target.'cfg(windows)'.dependencies"]
)
@pytest.mark.parametrize("optional", [False, True])
def test_selection_follows_transitive_local_dependency_kinds(
    repo: Repository, table: str, optional: bool
) -> None:
    repo.write("shared/base/Cargo.toml", '[package]\nname = "base"\n')
    repo.write("shared/base/src/lib.rs", "pub fn base() {}\n")
    repo.write(
        "shared/middle/Cargo.toml",
        '[package]\nname = "middle"\n[dependencies]\nbase = { path = "../base" }\n',
    )
    repo.write("shared/middle/src/lib.rs", "pub fn middle() {}\n")
    repo.write(
        "Cargo.toml",
        '[workspace]\nmembers = ["biggie", "coreutils/*", "utils/*", "shared/*"]\n'
        'exclude = ["coreutils/excluded"]\n',
    )
    repo.write(
        "coreutils/echor/Cargo.toml",
        f'[package]\nname = "echor"\n[{table}]\nmiddle = {{ path = "../../shared/middle"'
        + (", optional = true" if optional else "")
        + " }\n",
    )
    repo.base = repo.commit()
    repo.write("shared/base/src/lib.rs", "pub fn base() { } // changed\n")
    repo.commit()
    assert repo.selection()["binaries"] == ["echor"]


def test_parent_named_docs_does_not_hide_moved_cli_source(repo: Repository) -> None:
    (repo.root / "docs/commands").mkdir(parents=True)
    repo.git("mv", "coreutils/echor", "docs/commands/echor")
    repo.write(
        "Cargo.toml",
        '[workspace]\nmembers = ["biggie", "coreutils/*", "utils/*", "docs/commands/echor"]\nexclude = ["coreutils/excluded"]\n',
    )
    repo.base = repo.commit()
    repo.write("docs/commands/echor/src/main.rs", "fn main() {} // changed\n")
    repo.commit()
    assert repo.selection()["binaries"] == ["echor"]


def test_unowned_removed_rust_source_selects_conservatively(repo: Repository) -> None:
    repo.write("retired/src/old.rs", "// retired source\n")
    repo.base = repo.commit()
    (repo.root / "retired/src/old.rs").unlink()
    repo.commit()
    assert repo.selection()["suites"] == ALL_SUITES
