"""Reporter regressions using real ZIP/JSON data and a fake GitHub transport."""

import io
import json
import struct
import unittest
import zipfile
from typing import cast

import report

REPO = "di-rs/clis"
SHA = "a" * 40
BASE = f"repos/{REPO}"


def archive(files: dict[str, object]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
        for name, value in files.items():
            target.writestr(name, value if isinstance(value, str) else json.dumps(value))
    return buffer.getvalue()


def benchmark(mean: object = 0.002, runs: int = 1) -> dict[str, object]:
    return {
        "schema_version": 2,
        "results": [
            {
                "name": name,
                "summary": {"time_wall_clock": {"unit": "second", "mean": time, "count": runs}},
            }
            for name, time in (("reference", 0.004), ("candidate", mean))
        ],
    }


def suite_files(mean: object = 0.002, runs: int = 1) -> dict[str, object]:
    return {
        "coreutils-tailr-benches-cli-bench.toml/suite.toml": 'binary = "tailr"\n',
        "coreutils-tailr-benches-cli-bench.toml/metadata.json": {
            "status": "complete",
            "cases": ["last-lines"],
            "completed_cases": ["last-lines"],
            "runs": runs,
            "warmup": 0,
            "executables": {"candidate": "/tmp/ignore", "baseline": "/usr/bin/tail"},
        },
        "coreutils-tailr-benches-cli-bench.toml/last-lines.json": benchmark(mean, runs),
        "coreutils-tailr-benches-cli-bench.toml/last-lines.md": "@everyone [ignore](evil)",
    }


def job(name: str, start: str, end: str) -> dict[str, object]:
    return {
        "name": name,
        "conclusion": "success",
        "started_at": f"2026-10-13T00:{start}Z",
        "completed_at": f"2026-10-13T00:{end}Z",
        "steps": [
            {
                "name": "Build selected CLIs",
                "started_at": "2026-10-13T00:01:00Z",
                "completed_at": "2026-10-13T00:02:00Z",
            },
            {
                "name": "Run suites",
                "started_at": "2026-10-13T00:02:00Z",
                "completed_at": "2026-10-13T00:02:30Z",
            },
        ],
    }


class FakeGitHub(report.GitHub):
    def __init__(self) -> None:
        self.run: dict[str, object] = {
            "id": 500,
            "run_number": 10,
            "run_attempt": 1,
            "workflow_id": 42,
            "name": "CLI benchmark suites",
            "path": ".github/workflows/cli-bench.yml",
            "event": "pull_request",
            "status": "completed",
            "conclusion": "success",
            "repository": {"full_name": REPO},
            "head_sha": SHA,
            "pull_requests": [{"number": 7}],
        }
        self.pull: dict[str, object] = {
            "number": 7,
            "state": "open",
            "head": {"sha": SHA},
            "base": {"repo": {"full_name": REPO}, "ref": "master"},
        }
        self.jobs = [
            job("select", "00:00", "00:10"),
            job("suites (ubuntu-24.04)", "00:10", "03:10"),
            job("suites (macos-15)", "00:20", "04:20"),
        ]
        self.comments: list[dict[str, object]] = []
        self.responses: dict[str, object] = {
            f"{BASE}/actions/runs/500": self.run,
            f"{BASE}/actions/workflows/cli-bench.yml": {
                "id": 42,
                "path": ".github/workflows/cli-bench.yml",
            },
            f"{BASE}/pulls/7": self.pull,
            f"{BASE}/commits/{SHA}/pulls?per_page=100&page=1": [{"number": 7}],
            f"{BASE}/actions/runs/500/attempts/1/jobs?per_page=100&page=1": {"jobs": self.jobs},
            f"{BASE}/issues/7/comments?per_page=100&page=1": self.comments,
            f"{BASE}/actions/runs/500/artifacts?per_page=100&page=1": {
                "artifacts": [
                    {
                        "id": 1,
                        "name": "cli-bench-ubuntu-24.04",
                        "size_in_bytes": 2000,
                        "created_at": "2026-10-13T00:03:00Z",
                    },
                    {"id": 2, "name": "cli-bench-selection", "size_in_bytes": 200},
                ]
            },
            f"{BASE}/actions/artifacts/1/zip": archive(suite_files()),
            f"{BASE}/actions/artifacts/2/zip": archive(
                {"selection.json": {"binaries": ["tailr"], "runs": 1, "warmup": 0}}
            ),
        }

    def request(
        self, path: str, *, method: str = "GET", body: object = None, limit: int = 0
    ) -> bytes:
        if method == "POST":
            self.comments.append(
                {"id": 90, "body": cast(dict[str, object], body)["body"], "user": {"login": "bot"}}
            )
            return b"{}"
        if method == "PATCH":
            comment_id = int(path.rsplit("/", 1)[1])
            comment = next(item for item in self.comments if item["id"] == comment_id)
            comment["body"] = cast(dict[str, object], body)["body"]
            return b"{}"
        value = self.responses[path]
        return value if isinstance(value, bytes) else json.dumps(value).encode()


class ReportTests(unittest.TestCase):
    def test_renders_real_means_ratio_smoke_and_parallel_runner_timing(self) -> None:
        result = report.generate(FakeGitHub(), REPO, 500)
        assert result is not None
        self.assertIn("`aaaaaaaaaaaa`", result)
        self.assertIn("https://github.com/di-rs/clis/actions/runs/500", result)
        self.assertIn("tailr", result)
        self.assertIn("2.000 ms | 4.000 ms | 0.50×", result)
        self.assertIn("Smoke: 1 measured run", result)
        self.assertIn("4m 20s elapsed", result)
        self.assertIn("7m 10s summed runner time", result)
        self.assertIn("3m 00s | 1m 00s | 30s", result)
        self.assertNotIn("@everyone", result)

    def test_rejects_nonfinite_negative_boolean_or_string_means(self) -> None:
        for mean in (float("nan"), float("inf"), -1, True, "[click](evil)", 10**500, 5e-324):
            with self.subTest(mean=mean), self.assertRaises(ValueError):
                report.read_suites(archive(suite_files(mean)), "ubuntu-24.04")

    def test_rejects_traversal_oversized_members_and_markdown_identifiers(self) -> None:
        cases: list[dict[str, object]] = [
            {"../metadata.json": {}},
            {"large.json": "x" * (report.MAX_MEMBER + 1)},
            suite_files()
            | {"coreutils-tailr-benches-cli-bench.toml/suite.toml": 'binary = "@all"'},
        ]
        for files in cases:
            with self.subTest(files=list(files)), self.assertRaises(ValueError):
                report.read_suites(archive(files), "ubuntu-24.04")

    def test_rejects_inconsistent_measurement_count(self) -> None:
        files = suite_files()
        files["coreutils-tailr-benches-cli-bench.toml/last-lines.json"] = benchmark(runs=20)
        with self.assertRaises(ValueError):
            report.read_suites(archive(files), "ubuntu-24.04")

    def test_invalid_artifact_is_omitted_without_publishing_untrusted_error_text(self) -> None:
        api = FakeGitHub()
        api.responses[f"{BASE}/actions/artifacts/1/zip"] = archive(suite_files("@everyone"))
        result = report.generate(api, REPO, 500)
        assert result is not None
        self.assertIn("invalid artifact data omitted", result)
        self.assertNotIn("@everyone", result)

    def test_selection_with_excessive_identifiers_is_rejected(self) -> None:
        api = FakeGitHub()
        api.responses[f"{BASE}/actions/artifacts/2/zip"] = archive(
            {"selection.json": {"binaries": ["tailr"] * 10000, "runs": 1}}
        )
        result = report.generate(api, REPO, 500)
        assert result is not None
        self.assertIn("cli-bench-selection: invalid artifact data omitted", result)

    def test_encrypted_or_unknown_zip_compression_is_omitted(self) -> None:
        for offset, value in ((8, 1), (10, 99)):
            with self.subTest(offset=offset):
                api = FakeGitHub()
                data = bytearray(archive(suite_files()))
                struct.pack_into("<H", data, data.index(b"PK\x01\x02") + offset, value)
                api.responses[f"{BASE}/actions/artifacts/1/zip"] = bytes(data)
                result = report.generate(api, REPO, 500)
                assert result is not None
                self.assertIn("invalid artifact data omitted", result)

    def test_20_run_request_is_distinct_from_smoke(self) -> None:
        api = FakeGitHub()
        api.responses[f"{BASE}/actions/artifacts/1/zip"] = archive(suite_files(runs=20))
        api.responses[f"{BASE}/actions/artifacts/2/zip"] = archive(
            {"selection.json": {"binaries": ["tailr"], "runs": 20, "warmup": 3}}
        )
        result = report.generate(api, REPO, 500)
        assert result is not None
        self.assertIn("Requested: 20 measured runs", result)
        self.assertNotIn("Smoke:", result)

    def test_stale_cancelled_non_pr_and_wrong_workflow_runs_do_not_post(self) -> None:
        for change in (
            {"head_sha": "b" * 40},
            {"conclusion": "cancelled"},
            {"event": "push"},
            {"path": ".github/workflows/evil.yml"},
            {"repository": {"full_name": "other/repo"}},
            {"workflow_id": 123},
        ):
            with self.subTest(change=change):
                api = FakeGitHub()
                api.run.update(change)
                self.assertIsNone(report.generate(api, REPO, 500, author="bot"))
                self.assertEqual(api.comments, [])

    def test_fork_with_no_associated_pulls_uses_commit_lookup(self) -> None:
        api = FakeGitHub()
        api.run["pull_requests"] = []
        self.assertIsNotNone(report.generate(api, REPO, 500))

    def test_skipped_os_jobs_leave_existing_comment_untouched(self) -> None:
        api = FakeGitHub()
        for item in api.jobs[1:]:
            item["conclusion"] = "skipped"
        self.assertIsNone(report.generate(api, REPO, 500, author="bot"))
        self.assertEqual(api.comments, [])

    def test_build_failure_still_reports_selection_and_timing(self) -> None:
        api = FakeGitHub()
        api.run["conclusion"] = "failure"
        api.jobs[1]["conclusion"] = "failure"
        api.responses[f"{BASE}/actions/runs/500/artifacts?per_page=100&page=1"] = {
            "artifacts": [{"id": 2, "name": "cli-bench-selection", "size_in_bytes": 200}]
        }
        result = report.generate(api, REPO, 500)
        assert result is not None
        self.assertIn("failure", result)
        self.assertIn("tailr", result)
        self.assertIn("No completed benchmark measurements", result)

    def test_post_updates_only_own_comment_and_is_idempotent(self) -> None:
        api = FakeGitHub()
        body = report.generate(api, REPO, 500)
        api.comments.append({"id": 1, "body": body, "user": {"login": "someone-else"}})
        report.generate(api, REPO, 500, author="bot")
        report.generate(api, REPO, 500, author="bot")
        self.assertEqual(len(api.comments), 2)
        api.run["run_attempt"] = 2
        api.responses[f"{BASE}/actions/runs/500/attempts/2/jobs?per_page=100&page=1"] = {
            "jobs": api.jobs
        }
        report.generate(api, REPO, 500, author="bot")
        self.assertIn("attempt=2", str(api.comments[1]["body"]))
        self.assertNotIn("attempt=2", str(api.comments[0]["body"]))

    def test_older_rerun_cannot_overwrite_newer_comment(self) -> None:
        api = FakeGitHub()
        report.generate(api, REPO, 500, author="bot")
        newer = str(api.comments[0]["body"]).replace("attempt=1", "attempt=2")
        api.comments[0]["body"] = newer
        report.generate(api, REPO, 500, author="bot")
        self.assertEqual(api.comments[0]["body"], newer)

    def test_duplicate_artifact_names_use_latest_id_for_reruns(self) -> None:
        api = FakeGitHub()
        metadata = cast(
            dict[str, object],
            api.responses[f"{BASE}/actions/runs/500/artifacts?per_page=100&page=1"],
        )
        artifacts = cast(list[dict[str, object]], metadata["artifacts"])
        artifacts.append(artifacts[0] | {"id": 3})
        api.responses[f"{BASE}/actions/artifacts/3/zip"] = archive(suite_files(0.003))
        result = report.generate(api, REPO, 500)
        assert result is not None
        self.assertIn("3.000 ms | 4.000 ms | 0.75×", result)
        self.assertNotIn("2.000 ms", result)

    def test_prior_attempt_artifacts_are_not_shown_as_current_results(self) -> None:
        api = FakeGitHub()
        api.jobs[1]["started_at"] = "2026-10-13T00:04:00Z"
        api.jobs[1]["completed_at"] = "2026-10-13T00:05:00Z"
        result = report.generate(api, REPO, 500)
        assert result is not None
        self.assertIn("No completed benchmark measurements", result)
        self.assertNotIn("2.000 ms", result)


if __name__ == "__main__":
    unittest.main()
