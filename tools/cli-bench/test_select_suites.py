"""Selection regressions against real, temporary Git workspaces."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import TypedDict, cast

SELECTOR = Path(__file__).with_name("select_suites.py")
ALL_SUITES = [
    "biggie/benches/cli-bench.toml",
    "coreutils/echor/benches/cli-bench.toml",
    "coreutils/tailr/benches/cli-bench.toml",
    "coreutils/wcr/benches/cli-bench.toml",
]


class Selection(TypedDict):
    suites: list[str]
    packages: list[str]
    binaries: list[str]
    runs: int
    warmup: int
    reason: str


class SelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="cli-select-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        # Hooks export the parent repository's index/worktree; never reuse them in fixtures.
        git_local_env = subprocess.run(
            ["git", "rev-parse", "--local-env-vars"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        self.env = {key: value for key, value in os.environ.items() if key not in git_local_env} | {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
        }
        self.script = self.root / "tools/cli-bench/select_suites.py"
        self.script.parent.mkdir(parents=True)
        shutil.copyfile(SELECTOR, self.script)
        self.write(
            "Cargo.toml",
            '[workspace]\nmembers = ["biggie", "coreutils/*", "utils/*"]\n'
            'exclude = ["coreutils/excluded"]\n',
        )
        for name in ("biggie", "echor", "tailr", "wcr", "excluded"):
            directory = "biggie" if name == "biggie" else f"coreutils/{name}"
            self.write(f"{directory}/Cargo.toml", f'[package]\nname = "{name}"\n')
            self.write(f"{directory}/src/main.rs", "fn main() {}\n")
            self.write_suite(directory, name, generate=name == "tailr")
        self.write("utils/cli-tracing/Cargo.toml", '[package]\nname = "cli-tracing"\n')
        self.write("utils/cli-tracing/src/lib.rs", "// tracing\n")
        self.write("outside/benches/cli-bench.toml", 'binary = "outside"\n')
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Selector Tests")
        self.git("config", "user.email", "selector@example.invalid")
        self.base = self.commit()
        self.event = self.root / "event.json"
        self.summary = self.root / "summary.md"

    def write(self, path: str, content: str) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def write_suite(self, directory: str, binary: str, *, generate: bool = False) -> None:
        self.write(
            f"{directory}/benches/cli-bench.toml",
            f'binary = "{binary}"\n'
            + ('generate = [["text", "input.txt"]]\n' if generate else "generate = []\n")
            + '[[cases]]\nname = "sample"\nargs = []\n',
        )

    def git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=self.root, env=self.env, text=True, capture_output=True, check=True
        )
        return result.stdout.strip()

    def commit(self) -> str:
        self.git("add", ".")
        self.git("commit", "--quiet", "--allow-empty", "-m", "fixture")
        return self.git("rev-parse", "HEAD")

    def run_selector(
        self, body: str | None = None, *, previous_body: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        event: dict[str, object] = {"pull_request": {"body": body}}
        if previous_body is not None:
            event |= {"action": "edited", "changes": {"body": {"from": previous_body}}}
        self.event.write_text(json.dumps(event))
        return subprocess.run(
            [
                sys.executable,
                str(self.script),
                "--base",
                self.base,
                "--head",
                self.git("rev-parse", "HEAD"),
                "--event",
                str(self.event),
            ],
            cwd=self.root.parent,
            env=self.env | {"GITHUB_STEP_SUMMARY": str(self.summary)},
            capture_output=True,
            text=True,
        )

    def selection(self, body: str | None = None, *, previous_body: str | None = None) -> Selection:
        result = self.run_selector(body, previous_body=previous_body)
        self.assertEqual(result.returncode, 0, result.stderr)
        selected = cast(Selection, json.loads(result.stdout))
        self.assertIsInstance(selected["reason"], str)
        self.assertTrue(selected["reason"])
        return selected

    def test_whole_pr_uses_merge_base_and_includes_earlier_commits(self) -> None:
        self.git("checkout", "-b", "feature")
        self.write("coreutils/tailr/src/main.rs", "// changed\n")
        self.commit()
        self.write("README.md", "documentation in final PR commit\n")
        self.commit()
        self.git("checkout", "main")
        self.write("coreutils/echor/src/main.rs", "// target branch only\n")
        self.base = self.commit()
        self.git("checkout", "feature")
        result = self.selection()
        self.assertEqual(result["suites"], ["coreutils/tailr/benches/cli-bench.toml"])
        self.assertEqual(result["packages"], ["biggie", "tailr"])
        self.assertEqual(result["binaries"], ["tailr"])
        self.assertEqual((result["runs"], result["warmup"]), (1, 0))
        summary = self.summary.read_text()
        self.assertIn("tailr", summary)
        self.assertIn("1", summary)

    def test_deleted_source_still_selects_owner(self) -> None:
        (self.root / "coreutils/echor/src/main.rs").unlink()
        self.commit()
        self.assertEqual(self.selection()["packages"], ["echor"])

    def test_package_manifest_and_build_script_select_owner(self) -> None:
        for path, content in (
            ("coreutils/echor/Cargo.toml", '[package]\nname = "echor"\nversion = "0.1.1"\n'),
            ("coreutils/echor/build.rs", "fn main() {}\n"),
        ):
            with self.subTest(path=path):
                self.git("reset", "--hard", self.base)
                self.write(path, content)
                self.commit()
                self.assertEqual(self.selection()["packages"], ["echor"])

    def test_suite_change_selects_only_that_suite(self) -> None:
        self.write("coreutils/echor/benches/other.toml", 'binary = "echor"\n')
        self.base = self.commit()
        self.write_suite("coreutils/echor", "echor", generate=True)
        self.commit()
        result = self.selection()
        self.assertEqual(result["suites"], ["coreutils/echor/benches/cli-bench.toml"])
        self.assertEqual(result["packages"], ["biggie", "echor"])

    def test_deleted_suite_is_not_scheduled(self) -> None:
        (self.root / "coreutils/tailr/benches/cli-bench.toml").unlink()
        self.commit()
        self.assertEqual(self.selection()["suites"], [])

    def test_docs_tests_and_python_tool_configuration_skip_native_suites(self) -> None:
        for path in (
            "README.md",
            "coreutils/tailr/README.md",
            "coreutils/tailr/tests/cli.rs",
            "utils/cli-tracing/tests/basic.rs",
            "biggie/benches/performance.rs",
            "tools/cli-bench/test_bench.py",
            "tools/cli-bench/test_select_suites.py",
            "uv.lock",
            "pyproject.toml",
            "ruff.toml",
            "mypy.ini",
        ):
            self.write(path, "test or documentation change\n")
        self.commit()
        result = self.selection()
        self.assertEqual(result["suites"], [])
        self.assertEqual(result["packages"], [])

    def test_global_build_and_runner_changes_select_all_workspace_suites(self) -> None:
        for path in (
            "Cargo.toml",
            "Cargo.lock",
            "rust-toolchain.toml",
            ".cargo/config.toml",
            "tools/cli-bench/bench.py",
            "tools/cli-bench/select_suites.py",
            ".github/workflows/cli-bench.yml",
            ".python-version",
        ):
            with self.subTest(path=path):
                self.git("reset", "--hard", self.base)
                target = self.root / path
                previous = target.read_text() if target.exists() else ""
                self.write(path, previous + "\n# changed\n")
                self.commit()
                self.assertEqual(self.selection()["suites"], ALL_SUITES)

    def test_generator_and_tracing_production_changes_select_generated_suites(self) -> None:
        for path in ("biggie/src/main.rs", "utils/cli-tracing/src/lib.rs"):
            with self.subTest(path=path):
                self.git("reset", "--hard", self.base)
                self.write(path, "// changed\n")
                self.commit()
                result = self.selection()
                self.assertEqual(
                    result["suites"],
                    ["biggie/benches/cli-bench.toml", "coreutils/tailr/benches/cli-bench.toml"],
                )
                self.assertEqual(result["packages"], ["biggie", "tailr"])

    def test_other_shared_utility_production_changes_conservatively_select_all(self) -> None:
        self.write("utils/shared/Cargo.toml", '[package]\nname = "shared"\n')
        self.base = self.commit()
        for path, content in (
            ("utils/shared/src/lib.rs", "// shared helper\n"),
            ("utils/shared/build.rs", "fn main() {}\n"),
            ("utils/shared/Cargo.toml", '[package]\nname = "shared"\nversion = "0.1.1"\n'),
        ):
            with self.subTest(path=path):
                self.git("reset", "--hard", self.base)
                self.write(path, content)
                self.commit()
                self.assertEqual(self.selection()["suites"], ALL_SUITES)

    def test_empty_and_auto_requests_use_affected_smoke_selection(self) -> None:
        self.write("coreutils/echor/src/main.rs", "// changed\n")
        self.commit()
        for body in (None, "", "Some description", "Benchmark-CLIs:", "Benchmark-CLIs: auto"):
            with self.subTest(body=body):
                result = self.selection(body)
                self.assertEqual(result["packages"], ["echor"])
                self.assertEqual((result["runs"], result["warmup"]), (1, 0))

    def test_explicit_request_overrides_affected_suites_and_uses_full_runs(self) -> None:
        self.write("coreutils/echor/src/main.rs", "// changed\n")
        self.commit()
        result = self.selection("Description\n\nBenchmark-CLIs: tailr, wcr\n\nMore details")
        self.assertEqual(
            result["suites"],
            ["coreutils/tailr/benches/cli-bench.toml", "coreutils/wcr/benches/cli-bench.toml"],
        )
        self.assertEqual(result["packages"], ["biggie", "tailr", "wcr"])
        self.assertEqual((result["runs"], result["warmup"]), (20, 3))

    def test_all_request_uses_full_runs_even_without_code_changes(self) -> None:
        result = self.selection("Benchmark-CLIs: all")
        self.assertEqual(result["suites"], ALL_SUITES)
        self.assertEqual((result["runs"], result["warmup"]), (20, 3))

    def test_request_must_be_a_standalone_field(self) -> None:
        result = self.selection("Please use Benchmark-CLIs: tailr\n> Benchmark-CLIs: wcr")
        self.assertEqual(result["suites"], [])

    def test_unrelated_body_edit_does_not_repeat_previous_selection(self) -> None:
        self.write("coreutils/echor/src/main.rs", "// changed\n")
        self.commit()
        for previous, current in (
            ("Before", "After"),
            ("Before", "After\nBenchmark-CLIs: auto"),
            ("Benchmark-CLIs: tailr,wcr", "After\nBenchmark-CLIs: wcr, tailr"),
        ):
            with self.subTest(previous=previous):
                result = self.selection(current, previous_body=previous)
                self.assertEqual(result["suites"], [])
                self.assertEqual(result["packages"], [])
                self.assertEqual(result["binaries"], [])
                self.assertEqual((result["runs"], result["warmup"]), (1, 0))

    def test_body_edit_changes_request_or_repairs_invalid_request(self) -> None:
        for previous in (
            "Benchmark-CLIs: tailr",
            "Benchmark-CLIs: invalid",
            "Benchmark-CLIs: tailr\nBenchmark-CLIs: tailr",
        ):
            with self.subTest(previous=previous):
                result = self.selection("Benchmark-CLIs: wcr", previous_body=previous)
                self.assertEqual(result["binaries"], ["wcr"])
                self.assertEqual(result["packages"], ["wcr"])
                self.assertEqual((result["runs"], result["warmup"]), (20, 3))

    def test_invalid_and_duplicate_fields_fail_without_selection(self) -> None:
        for body, diagnostic in (
            ("Benchmark-CLIs: missing", "missing"),
            ("Benchmark-CLIs: excluded", "excluded"),
            ("Benchmark-CLIs: tailr,", "Benchmark-CLIs"),
            ("Benchmark-CLIs: all,tailr", "all"),
            ("Benchmark-CLIs: tailr\nBenchmark-CLIs: wcr", "duplicate"),
        ):
            with self.subTest(body=body):
                result = self.run_selector(body)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertIn(diagnostic, result.stderr)

    def test_suite_binary_must_match_owning_package_even_when_unselected(self) -> None:
        self.write_suite("coreutils/echor", "other-binary")
        self.commit()
        result = self.run_selector("Benchmark-CLIs: wcr")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("other-binary", result.stderr)
        self.assertIn("echor", result.stderr)


if __name__ == "__main__":
    unittest.main()
