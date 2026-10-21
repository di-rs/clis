"""Structured benchmark output remains parseable despite child tool chatter."""

import json
import subprocess
import sys

from conftest import RUNNER, Bench


def test_json_run_and_list(bench: Bench) -> None:
    bench.write_suite(
        'name = "sample"\nargs = []\nprepare = "printf prepare-output"\ncheck = "printf check-output"'
    )
    listed = subprocess.run(
        [sys.executable, str(RUNNER), str(bench.suite), "--json", "--list"],
        capture_output=True,
        text=True,
    )
    assert listed.returncode == 0
    assert json.loads(listed.stdout)["data"]["cases"][0]["name"] == "sample"
    assert not bench.output.exists()
    result = bench.run_bench("--json")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["data"]["completed_cases"] == ["sample"]
    assert "Benchmarking" in result.stderr


def test_json_doctor_and_argument_errors(bench: Bench) -> None:
    bench.write_suite()
    result = bench.run_bench("--json", "--doctor")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["data"]["auth"] == "not_required"
    assert not bench.output.exists()
    result = bench.run_bench("--json", "--unknown")
    assert result.returncode == 2
    assert json.loads(result.stdout)["error"]["kind"] == "arguments"
    assert result.stderr == ""
    result = bench.run_bench("--json", "--doctor", "--hyperfine", "/missing/hyperfine")
    assert result.returncode == 1
    assert json.loads(result.stdout)["ok"] is False
    assert not bench.output.exists()
