"""Reporter regressions using real ZIP/JSON data and a fake GitHub transport."""

import struct
from typing import cast

import pytest
import report
from conftest import BASE, REPO, FakeGitHub, archive, benchmark, suite_files


def test_renders_real_means_ratio_smoke_and_parallel_runner_timing(github: FakeGitHub) -> None:
    result = report.generate(github, REPO, 500)
    assert result is not None
    assert "`aaaaaaaaaaaa`" in result
    assert "https://github.com/di-rs/clis/actions/runs/500" in result
    assert "tailr" in result
    assert "2.000 ms | 4.000 ms | 0.50×" in result
    assert "Smoke: 1 measured run" in result
    assert "4m 20s elapsed" in result
    assert "7m 10s summed runner time" in result
    assert "3m 00s | 1m 00s | 30s" in result
    assert "@everyone" not in result


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
    assert "Requested: 20 measured runs" in result
    assert "Smoke:" not in result


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
    assert "failure" in result
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
