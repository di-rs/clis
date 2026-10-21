"""Run the workflow's suite loop against controlled benchmark exit statuses."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from conftest import TOOLS, Repository


def test_benchmark_workflow_cannot_run_on_pr_edits_or_with_empty_selection() -> None:
    workflow = yaml.safe_load((TOOLS.parents[1] / ".github/workflows/cli-bench.yml").read_text())
    triggers = workflow[True]  # PyYAML reads the YAML 1.1 key "on" as True.
    assert set(triggers) == {"pull_request"}
    assert "edited" not in triggers["pull_request"]["types"]
    assert workflow["jobs"]["suites"]["if"] == "needs.select.outputs.suites != '[]'"


@pytest.mark.parametrize("job", ["select", "suites"])
def test_checkout_matches_pr_dependency_graph_when_base_adds_a_package(
    repo: Repository, job: str
) -> None:
    repo.git("checkout", "-b", "feature")
    repo.write("buildins/tailr/src/main.rs", "// changed\n")
    head = repo.commit()
    repo.git("checkout", "main")
    repo.write("buildins/newcli/Cargo.toml", '[package]\nname = "newcli"\n')
    repo.write("buildins/newcli/src/main.rs", "fn main() {}\n")
    repo.lock({name: [] for name in ("biggie", "echor", "tailr", "wcr", "newcli")})
    repo.base = repo.commit()
    repo.git("merge", "--no-edit", "feature")
    merge = repo.git("rev-parse", "HEAD")
    workflow = yaml.safe_load((TOOLS.parents[1] / ".github/workflows/cli-bench.yml").read_text())
    checkout = workflow["jobs"][job]["steps"][0]["with"]
    revisions = {"${{ github.sha }}": merge, "${{ github.event.pull_request.head.sha }}": head}
    repo.git("checkout", "--detach", revisions[checkout.get("ref", "${{ github.sha }}")])
    event = repo.root / "event.json"
    event.write_text('{"pull_request": {"body": "Benchmark-CLIs: auto"}}')
    result = subprocess.run(
        [
            sys.executable,
            str(repo.root / "tools/cli-bench/select_suites.py"),
            "--base",
            repo.base,
            "--head",
            head,
            "--event",
            str(event),
        ],
        cwd=repo.root,
        env=repo.env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["binaries"] == ["tailr"]


@pytest.mark.parametrize("failed", [[], ["one/suite.toml"], ["one/suite.toml", "three/suite.toml"]])
def test_runs_every_suite_before_reporting_failures(
    suite_workflow: Path, failed: list[str]
) -> None:
    suites = ["one/suite.toml", "two/suite.toml", "three/suite.toml"]
    # Simulate report directories restored alongside cached Cargo build artifacts.
    for suite in suites:
        stale = suite_workflow.parent / "target/benchmarks" / suite.replace("/", "-")
        stale.mkdir(parents=True)
        (stale / "sentinel").write_text("cached report")
    output = suite_workflow.parent / "runner-temp/current-run"
    result = subprocess.run(
        [sys.executable, str(suite_workflow)],
        cwd=suite_workflow.parent,
        env=os.environ
        | {
            "SUITES": json.dumps(suites),
            "RUNS": "20",
            "WARMUP": "3",
            "FAILED_SUITES": json.dumps(failed),
            "BENCHMARK_OUTPUT": str(output),
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
        str(output / "one-suite.toml"),
    ]
    assert result.returncode == (1 if failed else 0), result.stderr
    for suite in suites:
        assert (output / suite.replace("/", "-")).is_dir()
        stale = suite_workflow.parent / "target/benchmarks" / suite.replace("/", "-")
        assert (stale / "sentinel").read_text() == "cached report"
    if failed:
        assert "Failed suites:" in result.stderr
        assert all(suite in result.stderr for suite in failed)
