"""Run the workflow's suite loop against controlled benchmark exit statuses."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("failed", [[], ["one/suite.toml"], ["one/suite.toml", "three/suite.toml"]])
def test_runs_every_suite_before_reporting_failures(
    suite_workflow: Path, failed: list[str]
) -> None:
    suites = ["one/suite.toml", "two/suite.toml", "three/suite.toml"]
    result = subprocess.run(
        [sys.executable, str(suite_workflow)],
        cwd=suite_workflow.parent,
        env=os.environ
        | {
            "SUITES": json.dumps(suites),
            "RUNS": "20",
            "WARMUP": "3",
            "FAILED_SUITES": json.dumps(failed),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    calls = [
        json.loads(line)
        for line in (suite_workflow.parent / "invocations.jsonl").read_text().splitlines()
    ]
    assert [call[0] for call in calls] == suites
    assert calls[0] == [
        "one/suite.toml",
        "--warmup",
        "3",
        "--runs",
        "20",
        "--output",
        "target/benchmarks/one-suite.toml",
    ]
    assert result.returncode == (1 if failed else 0), result.stderr
    if failed:
        assert "Failed suites:" in result.stderr
        assert all(suite in result.stderr for suite in failed)
