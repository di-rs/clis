"""Process-level regressions; fixture executables replace external tools only."""

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import RUNNER, Bench


def assert_failed_unpublished(bench: Bench, result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode != 0, result.stdout
    assert not (bench.output / "sample.json").exists()
    assert not (bench.output / "sample.md").exists()
    assert (bench.output / "metadata.json").exists(), result.stderr
    metadata = json.loads((bench.output / "metadata.json").read_text())
    assert metadata["status"] == "failed"


def test_list_does_not_require_any_executables(bench: Bench) -> None:
    bench.write_suite()
    result = subprocess.run(
        [sys.executable, str(RUNNER), str(bench.suite), "--list"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["sample"]
    assert not bench.output.exists()


def test_generation_stdin_literal_arguments_and_old_binary_comparison(bench: Bench) -> None:
    body = """
with open(os.environ['TRACE'], 'a') as trace:
    trace.write(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd(), 'locale': os.environ['LC_ALL'],
        'logging': [os.environ.get('CLIS_LOG_LEVEL'), os.environ.get('CLIS_LOG_STDERR')]}) + '\\n')
sys.stdout.buffer.write(sys.stdin.buffer.read())
"""
    bench.candidate = bench.executable("candidate 'quoted'", body)
    bench.baseline = bench.executable("baseline", body)
    bench.env.update(CLIS_LOG_LEVEL="debug", CLIS_LOG_STDERR="trace")
    bench.write_suite(
        'name = "sample"\nargs = ["space \' $HOME *"]\n'
        'reference_args = ["different BSD arguments"]\nstdin = "input file.txt"',
        'generate = [["input file.txt", "payload"]]\n[env]\nLC_ALL = "C"',
    )
    result = bench.run_bench()
    assert result.returncode == 0, result.stderr
    events = bench.events()
    assert sum("generate" in event for event in events) == 1
    calls = [event for event in events if "argv" in event]
    assert [event["argv"] for event in calls] == [["space ' $HOME *"]] * 2
    assert [event["locale"] for event in calls] == ["C", "C"]
    assert all(event["logging"] == ["off", "off"] for event in events)
    assert all(not Path(event["cwd"]).exists() for event in events)
    engine = next(event["hyperfine"] for event in events if "hyperfine" in event)
    commands = engine[engine.index("--") + 1 :]
    assert [shlex.split(command) for command in commands] == [
        [str(bench.baseline), "space ' $HOME *"],
        [str(bench.candidate), "space ' $HOME *"],
    ]
    assert Path(engine[engine.index("--input") + 1]).name == "input file.txt"
    assert "--output=pipe" in engine
    assert "--shell=none" in engine
    assert (bench.output / "sample.json").is_file()
    assert (bench.output / "suite.toml").read_bytes() == bench.suite.read_bytes()
    assert json.loads((bench.output / "metadata.json").read_text())["status"] == "complete"


def test_reference_override_uses_reference_arguments(bench: Bench) -> None:
    bench.candidate = bench.executable(
        "candidate", "assert sys.argv[1:] == ['candidate']; print('same')"
    )
    bench.baseline = bench.executable("reference", "assert sys.argv[1:] == ['BSD']; print('same')")
    bench.write_suite('name = "sample"\nargs = ["candidate"]\nreference_args = ["BSD"]')
    result = bench.run_bench("--reference", str(bench.baseline), compare=False)
    assert result.returncode == 0, result.stderr


def test_stdin_named_null_is_a_file_not_hyperfine_null_device(bench: Bench) -> None:
    bench.write_suite(
        'name = "sample"\nargs = []\nstdin = "null"', 'generate = [["null", "payload"]]'
    )
    result = bench.run_bench()
    assert result.returncode == 0, result.stderr
    event = next(event for event in bench.events() if "hyperfine" in event)
    engine = event["hyperfine"]
    assert engine[engine.index("--input") + 1] == str(Path(event["cwd"]) / "null")


def test_stdout_mismatch_stops_before_timing(bench: Bench) -> None:
    bench.write_suite()
    bench.baseline = bench.executable("different", "print('different')")
    result = bench.run_bench()
    assert_failed_unpublished(bench, result)
    assert "stdout" in result.stderr
    assert not any("hyperfine" in event for event in bench.events())


def test_unexpected_exit_reports_stderr_and_stops_before_timing(bench: Bench) -> None:
    bench.write_suite()
    bench.candidate = bench.executable(
        "failure", "print('specific failure', file=sys.stderr); sys.exit(3)"
    )
    result = bench.run_bench()
    assert_failed_unpublished(bench, result)
    assert "specific failure" in result.stderr
    assert not any("hyperfine" in event for event in bench.events())


def test_prepare_resets_each_preflight_and_check_verifies_effect(bench: Bench) -> None:
    body = "import pathlib; pathlib.Path('created').mkdir(); print('same')"
    bench.candidate = bench.executable("candidate", body)
    bench.baseline = bench.executable("baseline", body)
    bench.write_suite(
        'name = "sample"\nargs = []\nprepare = "rm -rf created"\ncheck = "test -d created"'
    )
    result = bench.run_bench()
    assert result.returncode == 0, result.stderr
    engine = next(event["hyperfine"] for event in bench.events() if "hyperfine" in event)
    assert engine[engine.index("--prepare") + 1] == "rm -rf created"


def test_failed_filesystem_check_stops_before_timing(bench: Bench) -> None:
    bench.write_suite('name = "sample"\nargs = []\ncheck = "test -d missing"')
    result = bench.run_bench()
    assert_failed_unpublished(bench, result)
    assert not any("hyperfine" in event for event in bench.events())


@pytest.mark.parametrize("mode", ["failure", "partial", "wrong-exit"])
def test_failed_partial_or_wrong_exit_reports_are_not_published(bench: Bench, mode: str) -> None:
    bench.write_suite()
    bench.env["FAKE_MODE"] = mode
    assert_failed_unpublished(bench, bench.run_bench())


def test_nonzero_expected_exit_must_match_measured_exit(bench: Bench) -> None:
    bench.write_suite('name = "sample"\nargs = []\nexpected_exit = 1')
    bench.candidate = bench.executable("no-match", "sys.exit(1)")
    result = bench.run_bench(compare=False)
    assert result.returncode == 0, result.stderr
    engine = next(event["hyperfine"] for event in bench.events() if "hyperfine" in event)
    assert "--ignore-failure=1" in engine
    bench.output = bench.root / "wrong-zero"
    bench.env["FAKE_MODE"] = "wrong-exit"
    assert_failed_unpublished(bench, bench.run_bench(compare=False))


@pytest.mark.parametrize(
    "case",
    [
        pytest.param('name = "sample"\nargs = []\nargz = []', id="unknown-field"),
        pytest.param('name = "sample"\nargs = []\nexpected_exit = true', id="boolean-exit"),
        pytest.param('name = "../escape"\nargs = []', id="escaping-name"),
        pytest.param('name = "sample"\nargs = []\nstdin = "../escape"', id="escaping-stdin"),
        pytest.param(
            'name = "sample"\nargs = []\n[[cases]]\nname = "sample"\nargs = []',
            id="duplicate-name",
        ),
        pytest.param('name = "metadata"\nargs = []', id="reserved-name"),
    ],
)
def test_invalid_suite_fails_without_running_tools(bench: Bench, case: str) -> None:
    bench.write_suite(case)
    result = bench.run_bench()
    assert result.returncode != 0
    assert not bench.output.exists()
    assert not bench.events()


def test_unknown_selection_fails_without_running_tools(bench: Bench) -> None:
    bench.write_suite()
    assert bench.run_bench("--case", "unknown").returncode != 0
    assert not bench.output.exists()
    assert not bench.events()


def test_existing_output_is_not_overwritten(bench: Bench) -> None:
    bench.write_suite()
    bench.output.mkdir()
    sentinel = bench.output / "metadata.json"
    sentinel.write_text("existing")
    result = bench.run_bench()
    assert result.returncode != 0
    assert sentinel.read_text() == "existing"
    assert not bench.events()
