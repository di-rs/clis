"""Reporter regressions using real ZIP/JSON data and a fake GitHub transport."""

import os
import struct
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
import report
from conftest import BASE, REPO, FakeGitHub, archive, benchmark, suite_files


@pytest.mark.parametrize("mode", ["no-output", "stdout-closed", "trickle"])
def test_github_request_deadline_kills_and_reaps_stalled_process(
    gh_process: Callable[[str], Path], monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    pid_path = gh_process(
        f"mode = {mode!r}\n"
        "if mode == 'stdout-closed':\n    os.close(1)\n"
        "if mode == 'trickle':\n"
        "    for _ in range(20):\n        os.write(1, b'x')\n        time.sleep(0.1)\n"
        "else:\n    time.sleep(2)\n"
    )
    monkeypatch.setattr(report, "REQUEST_TIMEOUT", 0.4)
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        report.GitHub().request("user")
    assert time.monotonic() - started < 1.5
    with pytest.raises(ChildProcessError):
        os.waitpid(int(pid_path.read_text()), os.WNOHANG)


@pytest.mark.parametrize("size", [16, 17], ids=["exact-limit", "over-limit"])
def test_github_request_keeps_byte_limit_and_reaps_child(
    gh_process: Callable[[str], Path], size: int
) -> None:
    pid_path = gh_process(
        f"os.write(1, b'x' * {size})\n" + ("time.sleep(2)\n" if size > 16 else "")
    )
    if size == 16:
        assert report.GitHub().request("user", limit=16) == b"x" * 16
    else:
        with pytest.raises(ValueError, match="exceeds size limit"):
            report.GitHub().request("user", limit=16)
    with pytest.raises(ChildProcessError):
        os.waitpid(int(pid_path.read_text()), os.WNOHANG)


def test_renders_real_means_ratio_smoke_and_parallel_runner_timing(github: FakeGitHub) -> None:
    result = report.generate(github, REPO, 500)
    assert result is not None
    assert "`aaaaaaaaaaaa`" in result
    assert "https://github.com/di-rs/clis/actions/runs/500" in result
    assert "tailr" in result
    assert "2.000 ms | 4.000 ms | 0.50×" in result
    assert "**Smoke check:** 1 measured run" in result
    assert "do not establish statistically significant performance changes" in result
    assert "4m 20s elapsed" in result
    assert "7m 10s summed runner time" in result
    assert "3m 00s | 1m 00s | 30s" in result
    assert "@everyone" not in result


def test_groups_both_platforms_by_cli_and_sorts_cases(github: FakeGitHub) -> None:
    suites = [
        report.Suite(
            binary,
            "complete",
            1,
            [
                report.Measurement(system, binary, case, 0.002, 0.004, "reference")
                for case in ("z-last", "a-first")
                for system in ("macos-15", "ubuntu-24.04")
            ],
        )
        for binary in ("wcr", "tailr")
    ]
    result = report.render(REPO, github.run, github.jobs, suites, {}, [])
    sections = result.split("<details>")[1:]
    assert len(sections) == 2
    for section, binary in zip(sections, ("tailr", "wcr"), strict=True):
        assert f"<strong>{binary}</strong> · 2 cases · Linux, macOS" in section
        assert "| Case | Platform | This PR | System | Relative time |" in section
        assert "| `a-first` | Linux | 2.000 ms | 4.000 ms | 0.50× |" in section
        assert section.index("| `a-first` | Linux") < section.index("| `a-first` | macOS")
        assert section.index("| `a-first` | macOS") < section.index("| `z-last` | Linux")
    reversed_suites = [
        report.Suite(suite.binary, suite.status, suite.runs, list(reversed(suite.rows)))
        for suite in reversed(suites)
    ]
    assert report.render(REPO, github.run, github.jobs, reversed_suites, {}, []) == result
    assert "| Linux | 3m 00s | 1m 00s | 30s | Passed |" in result
    assert "| macOS | 4m 00s | 1m 00s | 30s | Passed |" in result


@pytest.mark.parametrize(
    ("kind", "comparison", "header"),
    [
        ("reference", 0.004, "| Case | Platform | This PR | System | Relative time |"),
        ("baseline", 0.004, "| Case | Platform | This PR | Previous version | Relative time |"),
        ("candidate", None, "| Case | Platform | This PR |"),
    ],
)
def test_labels_comparison_source_and_omits_empty_columns(
    github: FakeGitHub, kind: str, comparison: float | None, header: str
) -> None:
    rows = [report.Measurement("ubuntu-24.04", "tailr", "sample", 0.002, comparison, kind)]
    suites = [report.Suite("tailr", "complete", 1, rows)]
    result = report.render(REPO, github.run, github.jobs, suites, {}, [])
    section = result.split("<details>")[1]
    assert header + "\n" in section
    assert "<strong>tailr</strong> · 1 case · Linux" in section
    if comparison is None:
        assert "Relative time" not in section
        assert "| `sample` | Linux | 2.000 ms |\n" in section
        assert "—" not in section
    else:
        assert "2.000 ms | 4.000 ms | 0.50×" in section


def test_grouped_results_keep_global_200_measurement_limit(github: FakeGitHub) -> None:
    suites = [
        report.Suite(
            binary,
            "complete",
            1,
            [
                report.Measurement(
                    "ubuntu-24.04", binary, f"case-{case:03}", 0.002, None, "candidate"
                )
                for case in range(102)
            ],
        )
        for binary in ("tailr", "wcr")
    ]
    result = report.render(REPO, github.run, github.jobs, suites, {}, [])
    assert result.count("| `case-") == 200
    assert result.count("<details>") == result.count("</details>") == 2
    assert "Showing the first 200 of 204 measurements" in result
    assert "full results are in the run artifacts" in result
    assert "| `case-098`" not in result.split("<strong>wcr</strong>")[1]


@pytest.mark.parametrize(
    "mean",
    [float("nan"), float("inf"), -1, True, "[click](evil)", 10**500, 5e-324],
    ids=["nan", "infinity", "negative", "boolean", "string", "huge-integer", "subnormal"],
)
def test_rejects_nonfinite_negative_boolean_or_string_means(mean: object) -> None:
    with pytest.raises(ValueError):
        report.read_suites(archive(suite_files(mean)), "ubuntu-24.04")


@pytest.mark.parametrize(
    "files",
    [
        {"../metadata.json": {}},
        {"large.json": "x" * (report.MAX_MEMBER + 1)},
        suite_files() | {"coreutils-tailr-benches-cli-bench.toml/suite.toml": 'binary = "@all"'},
    ],
    ids=["traversal", "oversized-member", "markdown-identifier"],
)
def test_rejects_traversal_oversized_members_and_markdown_identifiers(
    files: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        report.read_suites(archive(files), "ubuntu-24.04")


def test_rejects_inconsistent_measurement_count() -> None:
    files = suite_files()
    files["coreutils-tailr-benches-cli-bench.toml/last-lines.json"] = benchmark(runs=20)
    with pytest.raises(ValueError):
        report.read_suites(archive(files), "ubuntu-24.04")


def test_invalid_artifact_is_omitted_without_publishing_untrusted_error_text(
    github: FakeGitHub,
) -> None:
    github.responses[f"{BASE}/actions/artifacts/1/zip"] = archive(suite_files("@everyone"))
    result = report.generate(github, REPO, 500)
    assert result is not None
    assert "invalid artifact data omitted" in result
    assert "@everyone" not in result


def test_selection_with_excessive_identifiers_is_rejected(github: FakeGitHub) -> None:
    github.responses[f"{BASE}/actions/artifacts/2/zip"] = archive(
        {"selection.json": {"binaries": ["tailr"] * 10000, "runs": 1}}
    )
    result = report.generate(github, REPO, 500)
    assert result is not None
    assert "cli-bench-selection: invalid artifact data omitted" in result


@pytest.mark.parametrize(
    ("offset", "value"), [(8, 1), (10, 99)], ids=["encrypted", "unknown-compression"]
)
def test_encrypted_or_unknown_zip_compression_is_omitted(
    github: FakeGitHub, offset: int, value: int
) -> None:
    data = bytearray(archive(suite_files()))
    struct.pack_into("<H", data, data.index(b"PK\x01\x02") + offset, value)
    github.responses[f"{BASE}/actions/artifacts/1/zip"] = bytes(data)
    result = report.generate(github, REPO, 500)
    assert result is not None
    assert "invalid artifact data omitted" in result


def test_20_run_request_is_distinct_from_smoke(github: FakeGitHub) -> None:
    github.responses[f"{BASE}/actions/artifacts/1/zip"] = archive(suite_files(runs=20))
    github.responses[f"{BASE}/actions/artifacts/2/zip"] = archive(
        {"selection.json": {"binaries": ["tailr"], "runs": 20, "warmup": 3}}
    )
    result = report.generate(github, REPO, 500)
    assert result is not None
    assert "**Requested benchmark:** 20 measured runs" in result
    assert "Smoke check:" not in result


@pytest.mark.parametrize(
    "change",
    [
        {"head_sha": "b" * 40},
        {"conclusion": "cancelled"},
        {"event": "push"},
        {"path": ".github/workflows/evil.yml"},
        {"repository": {"full_name": "other/repo"}},
        {"workflow_id": 123},
    ],
    ids=["stale-sha", "cancelled", "push", "wrong-path", "wrong-repository", "wrong-workflow-id"],
)
def test_stale_cancelled_non_pr_and_wrong_workflow_runs_do_not_post(
    github: FakeGitHub, change: dict[str, object]
) -> None:
    github.run.update(change)
    assert report.generate(github, REPO, 500, author="bot") is None
    assert github.comments == []


def test_fork_with_no_associated_pulls_uses_commit_lookup(github: FakeGitHub) -> None:
    github.run["pull_requests"] = []
    assert report.generate(github, REPO, 500) is not None


def test_skipped_os_jobs_leave_existing_comment_untouched(github: FakeGitHub) -> None:
    for item in github.jobs[1:]:
        item["conclusion"] = "skipped"
    assert report.generate(github, REPO, 500, author="bot") is None
    assert github.comments == []


def test_build_failure_still_reports_selection_and_timing(github: FakeGitHub) -> None:
    github.run["conclusion"] = "failure"
    github.jobs[1]["conclusion"] = "failure"
    github.responses[f"{BASE}/actions/runs/500/artifacts?per_page=100&page=1"] = {
        "artifacts": [{"id": 2, "name": "cli-bench-selection", "size_in_bytes": 200}]
    }
    result = report.generate(github, REPO, 500)
    assert result is not None
    assert "**Failed**" in result
    assert "tailr" in result
    assert "No completed benchmark measurements" in result


def test_post_updates_only_own_comment_and_is_idempotent(github: FakeGitHub) -> None:
    body = report.generate(github, REPO, 500)
    github.comments.append({"id": 1, "body": body, "user": {"login": "someone-else"}})
    report.generate(github, REPO, 500, author="bot")
    report.generate(github, REPO, 500, author="bot")
    assert len(github.comments) == 2
    github.run["run_attempt"] = 2
    github.responses[f"{BASE}/actions/runs/500/attempts/2/jobs?per_page=100&page=1"] = {
        "jobs": github.jobs
    }
    report.generate(github, REPO, 500, author="bot")
    assert "attempt=2" in str(github.comments[1]["body"])
    assert "attempt=2" not in str(github.comments[0]["body"])


def test_older_rerun_cannot_overwrite_newer_comment(github: FakeGitHub) -> None:
    report.generate(github, REPO, 500, author="bot")
    newer = str(github.comments[0]["body"]).replace("attempt=1", "attempt=2")
    github.comments[0]["body"] = newer
    report.generate(github, REPO, 500, author="bot")
    assert github.comments[0]["body"] == newer


def test_duplicate_artifact_names_use_latest_id_for_reruns(github: FakeGitHub) -> None:
    metadata = cast(
        dict[str, object],
        github.responses[f"{BASE}/actions/runs/500/artifacts?per_page=100&page=1"],
    )
    artifacts = cast(list[dict[str, object]], metadata["artifacts"])
    artifacts.append(artifacts[0] | {"id": 3})
    github.responses[f"{BASE}/actions/artifacts/3/zip"] = archive(suite_files(0.003))
    result = report.generate(github, REPO, 500)
    assert result is not None
    assert "3.000 ms | 4.000 ms | 0.75×" in result
    assert "2.000 ms" not in result


def test_prior_attempt_artifacts_are_not_shown_as_current_results(github: FakeGitHub) -> None:
    github.jobs[1]["started_at"] = "2026-10-13T00:04:00Z"
    github.jobs[1]["completed_at"] = "2026-10-13T00:05:00Z"
    result = report.generate(github, REPO, 500)
    assert result is not None
    assert "No completed benchmark measurements" in result
    assert "2.000 ms" not in result
