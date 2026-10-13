"""Process-level regressions; fixture executables replace external tools only."""

import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import NotRequired, TypedDict, cast

RUNNER = Path(__file__).with_name("bench.py")


class Event(TypedDict):
    cwd: str
    generate: NotRequired[list[str]]
    hyperfine: NotRequired[list[str]]
    argv: NotRequired[list[str]]
    locale: NotRequired[str]


class BenchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="cli-bench-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.suite = self.root / "suite.toml"
        self.output = self.root / "results"
        self.trace = self.root / "trace.jsonl"
        self.env = os.environ | {"TRACE": str(self.trace)}
        self.candidate = self.executable("candidate 'quoted'", "print('same')")
        self.baseline = self.executable("baseline", "print('same')")
        self.biggie = self.executable(
            "biggie",
            """
import pathlib
pathlib.Path(sys.argv[1]).write_text(sys.argv[2])
with open(os.environ['TRACE'], 'a') as trace:
    trace.write(json.dumps({'generate': sys.argv[1:], 'cwd': os.getcwd()}) + '\\n')
""",
        )
        self.hyperfine = self.executable(
            "hyperfine",
            """
import pathlib
args = sys.argv[1:]
with open(os.environ['TRACE'], 'a') as trace:
    trace.write(json.dumps({'hyperfine': args, 'cwd': os.getcwd()}) + '\\n')
def option(name):
    return args[args.index(name) + 1]
commands = args[args.index('--') + 1:]
runs = int(option('--runs'))
status = next((int(a.split('=')[1]) for a in args if a.startswith('--ignore-failure=')), 0)
mode = os.environ.get('FAKE_MODE', '')
if mode == 'wrong-exit':
    status = 0 if status else 1
results = [{'command': command, 'measurements': [{'exit_code': status} for _ in range(runs)],
            'summary': {'time_wall_clock': {'count': runs, 'mean': 0.001}}}
           for command in commands]
if mode == 'partial':
    results.pop()
pathlib.Path(option('--export-json')).write_text(json.dumps({'schema_version': 2, 'results': results}))
pathlib.Path(option('--export-markdown')).write_text('| Command | Mean |\\n| --- | --- |\\n')
sys.exit(1 if mode == 'failure' else 0)
""",
            version="hyperfine 2.0.0",
        )

    def executable(self, name: str, body: str, version: str = "fixture 1.0") -> Path:
        path = self.root / name
        path.write_text(
            f"#!{sys.executable}\nimport json, os, sys\n"
            f"if sys.argv[1:] == ['--version']:\n    print({version!r})\n    sys.exit(0)\n"
            + body
            + "\n"
        )
        path.chmod(0o755)
        return path

    def write_suite(self, case: str = 'name = "sample"\nargs = []', top: str = "") -> None:
        self.suite.write_text('binary = "fixture"\n' + top + "\n[[cases]]\n" + case + "\n")

    def run_bench(self, *args: str, compare: bool = True) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            str(RUNNER),
            str(self.suite),
            "--candidate",
            str(self.candidate),
            "--biggie",
            str(self.biggie),
            "--hyperfine",
            str(self.hyperfine),
            "--output",
            str(self.output),
            "--runs",
            "2",
            "--warmup",
            "1",
        ]
        if compare:
            command += ["--baseline", str(self.baseline)]
        return subprocess.run(command + list(args), env=self.env, capture_output=True, text=True)

    def events(self) -> list[Event]:
        return (
            [cast(Event, json.loads(line)) for line in self.trace.read_text().splitlines()]
            if self.trace.exists()
            else []
        )

    def assert_failed_unpublished(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse((self.output / "sample.json").exists())
        self.assertFalse((self.output / "sample.md").exists())
        self.assertTrue((self.output / "metadata.json").exists(), result.stderr)
        metadata: dict[str, object] = json.loads((self.output / "metadata.json").read_text())
        self.assertEqual(metadata["status"], "failed")

    def test_list_does_not_require_any_executables(self) -> None:
        self.write_suite()
        result = subprocess.run(
            [sys.executable, str(RUNNER), str(self.suite), "--list"], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["sample"])
        self.assertFalse(self.output.exists())

    def test_generation_stdin_literal_arguments_and_old_binary_comparison(self) -> None:
        body = """
with open(os.environ['TRACE'], 'a') as trace:
    trace.write(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd(), 'locale': os.environ['LC_ALL']}) + '\\n')
sys.stdout.buffer.write(sys.stdin.buffer.read())
"""
        self.candidate = self.executable("candidate 'quoted'", body)
        self.baseline = self.executable("baseline", body)
        self.write_suite(
            'name = "sample"\nargs = ["space \' $HOME *"]\n'
            'reference_args = ["different BSD arguments"]\nstdin = "input file.txt"',
            'generate = [["input file.txt", "payload"]]\n[env]\nLC_ALL = "C"',
        )
        result = self.run_bench()
        self.assertEqual(result.returncode, 0, result.stderr)
        events = self.events()
        self.assertEqual(sum("generate" in event for event in events), 1)
        calls = [event for event in events if "argv" in event]
        self.assertEqual([event["argv"] for event in calls], [["space ' $HOME *"]] * 2)
        self.assertEqual([event["locale"] for event in calls], ["C", "C"])
        self.assertTrue(all(not Path(event["cwd"]).exists() for event in events))
        engine = next(event["hyperfine"] for event in events if "hyperfine" in event)
        commands = engine[engine.index("--") + 1 :]
        self.assertEqual(
            [shlex.split(command) for command in commands],
            [[str(self.baseline), "space ' $HOME *"], [str(self.candidate), "space ' $HOME *"]],
        )
        self.assertEqual(Path(engine[engine.index("--input") + 1]).name, "input file.txt")
        self.assertIn("--output=pipe", engine)
        self.assertIn("--shell=none", engine)
        self.assertTrue((self.output / "sample.json").is_file())
        self.assertEqual((self.output / "suite.toml").read_bytes(), self.suite.read_bytes())
        self.assertEqual(
            json.loads((self.output / "metadata.json").read_text())["status"], "complete"
        )

    def test_reference_override_uses_reference_arguments(self) -> None:
        self.candidate = self.executable(
            "candidate", "assert sys.argv[1:] == ['candidate']; print('same')"
        )
        self.baseline = self.executable(
            "reference", "assert sys.argv[1:] == ['BSD']; print('same')"
        )
        self.write_suite('name = "sample"\nargs = ["candidate"]\nreference_args = ["BSD"]')
        result = self.run_bench("--reference", str(self.baseline), compare=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_stdin_named_null_is_a_file_not_hyperfine_null_device(self) -> None:
        self.write_suite(
            'name = "sample"\nargs = []\nstdin = "null"', 'generate = [["null", "payload"]]'
        )
        result = self.run_bench()
        self.assertEqual(result.returncode, 0, result.stderr)
        event = next(event for event in self.events() if "hyperfine" in event)
        engine = event["hyperfine"]
        self.assertEqual(engine[engine.index("--input") + 1], str(Path(event["cwd"]) / "null"))

    def test_stdout_mismatch_stops_before_timing(self) -> None:
        self.write_suite()
        self.baseline = self.executable("different", "print('different')")
        result = self.run_bench()
        self.assert_failed_unpublished(result)
        self.assertIn("stdout", result.stderr)
        self.assertFalse(any("hyperfine" in event for event in self.events()))

    def test_unexpected_exit_reports_stderr_and_stops_before_timing(self) -> None:
        self.write_suite()
        self.candidate = self.executable(
            "failure", "print('specific failure', file=sys.stderr); sys.exit(3)"
        )
        result = self.run_bench()
        self.assert_failed_unpublished(result)
        self.assertIn("specific failure", result.stderr)
        self.assertFalse(any("hyperfine" in event for event in self.events()))

    def test_prepare_resets_each_preflight_and_check_verifies_effect(self) -> None:
        body = "import pathlib; pathlib.Path('created').mkdir(); print('same')"
        self.candidate = self.executable("candidate", body)
        self.baseline = self.executable("baseline", body)
        self.write_suite(
            'name = "sample"\nargs = []\nprepare = "rm -rf created"\ncheck = "test -d created"'
        )
        result = self.run_bench()
        self.assertEqual(result.returncode, 0, result.stderr)
        engine = next(event["hyperfine"] for event in self.events() if "hyperfine" in event)
        self.assertEqual(engine[engine.index("--prepare") + 1], "rm -rf created")

    def test_failed_filesystem_check_stops_before_timing(self) -> None:
        self.write_suite('name = "sample"\nargs = []\ncheck = "test -d missing"')
        result = self.run_bench()
        self.assert_failed_unpublished(result)
        self.assertFalse(any("hyperfine" in event for event in self.events()))

    def test_failed_partial_or_wrong_exit_reports_are_not_published(self) -> None:
        self.write_suite()
        for mode in ["failure", "partial", "wrong-exit"]:
            with self.subTest(mode=mode):
                self.output = self.root / mode
                self.env["FAKE_MODE"] = mode
                self.assert_failed_unpublished(self.run_bench())

    def test_nonzero_expected_exit_must_match_measured_exit(self) -> None:
        self.write_suite('name = "sample"\nargs = []\nexpected_exit = 1')
        self.candidate = self.executable("no-match", "sys.exit(1)")
        result = self.run_bench(compare=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        engine = next(event["hyperfine"] for event in self.events() if "hyperfine" in event)
        self.assertIn("--ignore-failure=1", engine)
        self.output = self.root / "wrong-zero"
        self.env["FAKE_MODE"] = "wrong-exit"
        self.assert_failed_unpublished(self.run_bench(compare=False))

    def test_invalid_suite_or_selection_fails_without_running_tools(self) -> None:
        bad_cases = [
            'name = "sample"\nargs = []\nargz = []',
            'name = "sample"\nargs = []\nexpected_exit = true',
            'name = "../escape"\nargs = []',
            'name = "sample"\nargs = []\nstdin = "../escape"',
            'name = "sample"\nargs = []\n[[cases]]\nname = "sample"\nargs = []',
            'name = "metadata"\nargs = []',
        ]
        for case in bad_cases:
            with self.subTest(case=case):
                self.write_suite(case)
                result = self.run_bench()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.output.exists())
                self.assertFalse(self.events())
        self.write_suite()
        self.assertNotEqual(self.run_bench("--case", "unknown").returncode, 0)
        self.assertFalse(self.output.exists())

    def test_existing_output_is_not_overwritten(self) -> None:
        self.write_suite()
        self.output.mkdir()
        sentinel = self.output / "metadata.json"
        sentinel.write_text("existing")
        result = self.run_bench()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sentinel.read_text(), "existing")
        self.assertFalse(self.events())


if __name__ == "__main__":
    unittest.main()
