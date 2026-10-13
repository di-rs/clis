#!/usr/bin/env python3
"""Render PR benchmark artifacts as data; optionally update the caller's sticky comment."""

import argparse
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import cast

MAX_JSON = 2 * 1024 * 1024
MAX_ZIP = 8 * 1024 * 1024
MAX_MEMBER = 1024 * 1024
SYSTEMS = ("ubuntu-24.04", "macos-15")
WORKFLOW = ".github/workflows/cli-bench.yml"
MARKER = re.compile(r"<!-- cli-bench-report number=(\d+) run=(\d+) attempt=(\d+) -->")


def table(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("expected an object")
    return cast(dict[str, object], value)


def array(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("expected an array")
    return cast(list[object], value)


def integer(value: object, maximum: int = 10**18, minimum: int = 1) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("invalid integer")
    return value


def identifier(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", value):
        raise ValueError("invalid identifier")
    return value


class GitHub:
    """The only network boundary. gh handles authentication and artifact redirects."""

    def request(
        self, path: str, *, method: str = "GET", body: object = None, limit: int = MAX_JSON
    ) -> bytes:
        command = ["gh", "api", "--hostname", "github.com", "--method", method, path]
        with tempfile.TemporaryFile() as payload, tempfile.TemporaryFile() as errors:
            if body is not None:
                payload.write(json.dumps(body).encode())
                payload.seek(0)
                command += ["--input", "-"]
            with subprocess.Popen(
                command, stdin=payload, stdout=subprocess.PIPE, stderr=errors
            ) as process:
                assert process.stdout is not None
                data = process.stdout.read(limit + 1)
                if len(data) > limit:
                    process.kill()
                    raise ValueError("GitHub response exceeds size limit")
                if process.wait(timeout=60):
                    raise ValueError("GitHub API request failed")
                return data

    def json(self, path: str) -> object:
        return cast(object, json.loads(self.request(path)))

    def pages(self, path: str, key: str | None = None) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        separator = "&" if "?" in path else "?"
        for page in range(1, 21):
            response = self.json(f"{path}{separator}per_page=100&page={page}")
            values = array(table(response)[key] if key else response)
            result.extend(table(value) for value in values)
            if len(values) < 100:
                return result
        raise ValueError("GitHub pagination limit exceeded")


def read_zip(data: bytes) -> dict[str, bytes]:
    if len(data) > MAX_ZIP:
        raise ValueError("archive exceeds size limit")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
        if len(members) > 512 or sum(item.file_size for item in members) > 32 * MAX_MEMBER:
            raise ValueError("archive contents exceed limits")
        files: dict[str, bytes] = {}
        for member in members:
            path = PurePosixPath(member.filename)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in member.filename
                or member.file_size > MAX_MEMBER
                or member.flag_bits & 1
                or member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                or (member.external_attr >> 16) & 0o170000 == 0o120000
                or member.filename in files
            ):
                raise ValueError("unsafe archive member")
            if not member.is_dir():
                files[member.filename] = archive.read(member)
        return files


@dataclass(frozen=True)
class Measurement:
    system: str
    binary: str
    case: str
    candidate: float
    comparison: float | None
    kind: str


@dataclass(frozen=True)
class Suite:
    binary: str
    status: str
    runs: int
    rows: list[Measurement]


def mean(result: dict[str, object], runs: int) -> float:
    metric = table(table(result["summary"])["time_wall_clock"])
    value = metric.get("mean")
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 1e-12 <= value < 86400
        or metric.get("unit") != "second"
        or integer(metric.get("count"), 1000) != runs
    ):
        raise ValueError("invalid wall-clock measurement")
    return float(value)


def read_suites(data: bytes, system: str) -> list[Suite]:
    files = read_zip(data)
    suites: list[Suite] = []
    for path in sorted(files):
        if PurePosixPath(path).name != "metadata.json":
            continue
        prefix = path.removesuffix("metadata.json")
        metadata = table(json.loads(files[path]))
        suite = tomllib.loads(files[prefix + "suite.toml"].decode())
        binary = identifier(suite.get("binary"))
        status = metadata.get("status")
        if status not in ("complete", "failed", "running"):
            raise ValueError("invalid suite status")
        runs = integer(metadata.get("runs"), 1000)
        cases = [identifier(case) for case in array(metadata.get("cases"))]
        completed = [identifier(case) for case in array(metadata.get("completed_cases"))]
        if (
            len(cases) > 200
            or len(set(completed)) != len(completed)
            or not set(completed) <= set(cases)
        ):
            raise ValueError("invalid completed cases")
        if status == "complete" and set(completed) != set(cases):
            raise ValueError("incomplete suite")
        rows: list[Measurement] = []
        for case in completed:
            document = table(json.loads(files[prefix + case + ".json"]))
            if document.get("schema_version") != 2:
                raise ValueError("expected hyperfine schema 2")
            results = [table(item) for item in array(document.get("results"))]
            names = [item.get("name") for item in results]
            if names not in (["candidate"], ["reference", "candidate"], ["baseline", "candidate"]):
                raise ValueError("unexpected benchmark roles")
            comparison = mean(results[0], runs) if len(results) == 2 else None
            rows.append(
                Measurement(
                    system, binary, case, mean(results[-1], runs), comparison, str(names[0])
                )
            )
        suites.append(Suite(binary, str(status), runs, rows))
    return suites


def seconds(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def duration(item: dict[str, object]) -> float:
    start, end = seconds(item.get("started_at")), seconds(item.get("completed_at"))
    return max(0, end - start) if start is not None and end is not None else 0


def clock(value: float) -> str:
    minutes, rest = divmod(round(value), 60)
    return f"{minutes}m {rest:02}s" if minutes else f"{rest}s"


def render(
    repo: str,
    run: dict[str, object],
    jobs: list[dict[str, object]],
    suites: list[Suite],
    selection: dict[str, object],
    warnings: list[str],
) -> str:
    number, run_id, attempt = (integer(run[key]) for key in ("run_number", "id", "run_attempt"))
    sha = str(run["head_sha"])
    binaries = [identifier(value) for value in array(selection.get("binaries", []))]
    binaries = sorted(set(binaries or [suite.binary for suite in suites]))[:64]
    counts = {suite.runs for suite in suites}
    if "runs" in selection:
        counts.add(integer(selection["runs"], 1000))
    mode = (
        "Smoke: 1 measured run per command."
        if counts == {1}
        else "Requested: 20 measured runs per command."
        if counts == {20}
        else "Measurement count unavailable or inconsistent."
    )
    status = identifier(run["conclusion"])
    lines = [
        f"<!-- cli-bench-report number={number} run={run_id} attempt={attempt} -->",
        "### CLI benchmark results",
        "",
        f"**{status}** · `{sha[:12]}` · "
        f"[Run {number}, attempt {attempt}](https://github.com/{repo}/actions/runs/{run_id}/attempts/{attempt})",
        "",
        "Selected CLIs: " + (", ".join(f"`{name}`" for name in binaries) or "unavailable") + ".",
        f"{mode} Ratios are descriptive, not a statistical regression test.",
        "",
        "| OS | Job | Build | Suites | Status |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for system in SYSTEMS:
        job = next((item for item in jobs if item.get("name") == f"suites ({system})"), None)
        if job is None:
            lines.append(f"| {system} | — | — | — | not run |")
            continue
        steps = [table(value) for value in array(job.get("steps", []))]
        times = [
            next((clock(duration(step)) for step in steps if step.get("name") == name), "—")
            for name in ("Build selected CLIs", "Run suites")
        ]
        lines.append(
            f"| {system} | {clock(duration(job))} | {times[0]} | {times[1]} | "
            f"{identifier(job.get('conclusion'))} |"
        )
    starts = [value for job in jobs if (value := seconds(job.get("started_at"))) is not None]
    ends = [value for job in jobs if (value := seconds(job.get("completed_at"))) is not None]
    elapsed = max(ends) - min(starts) if starts and ends else 0
    lines += [
        "",
        f"**{clock(elapsed)} elapsed** from first job start to last finish; "
        f"**{clock(sum(duration(job) for job in jobs))} summed runner time** "
        "(parallel jobs plus selection, excluding queue time).",
        "",
    ]
    rows = [row for suite in suites for row in suite.rows]
    if rows:
        lines += [
            "<details><summary>Native wall-clock means</summary>",
            "",
            "| OS | CLI / case | Candidate | Reference / baseline | Candidate ÷ comparison |",
            "| --- | --- | ---: | ---: | ---: |",
        ]
        for row in rows[:200]:
            comparison = f"{row.comparison * 1000:.3f} ms" if row.comparison else "—"
            ratio = f"{row.candidate / row.comparison:.2f}×" if row.comparison else "—"
            lines.append(
                f"| {row.system} | {row.binary} / {row.case} | {row.candidate * 1000:.3f} ms | "
                f"{comparison} | {ratio} |"
            )
        lines += ["", "Below 1× means the candidate took less wall time.", "", "</details>"]
        if len(rows) > 200:
            lines += ["", "Table limited to 200 cases; full results are in the run artifacts."]
    else:
        lines.append("No completed benchmark measurements were available.")
    if any(suite.status != "complete" for suite in suites):
        warnings.append("Some suites did not complete; only completed cases are shown.")
    lines.extend(["", *warnings])
    return "\n".join(lines).rstrip() + "\n"


def current_pull(api: GitHub, repo: str, number: int, sha: str) -> bool:
    pull = table(api.json(f"repos/{repo}/pulls/{number}"))
    base = table(pull.get("base"))
    return (
        pull.get("state") == "open"
        and table(pull.get("head")).get("sha") == sha
        and table(base.get("repo")).get("full_name") == repo
        and base.get("ref") == "master"
    )


def generate(api: GitHub, repo: str, run_id: int, *, author: str | None = None) -> str | None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("invalid repository")
    base = f"repos/{repo}"
    run = table(api.json(f"{base}/actions/runs/{integer(run_id)}"))
    if (
        run.get("id") != run_id
        or run.get("event") != "pull_request"
        or run.get("status") != "completed"
        or run.get("conclusion") in ("cancelled", "skipped")
        or run.get("path") != WORKFLOW
        or run.get("name") != "CLI benchmark suites"
        or table(run.get("repository")).get("full_name") != repo
    ):
        return None
    workflow = table(api.json(f"{base}/actions/workflows/cli-bench.yml"))
    if workflow.get("id") != run.get("workflow_id") or workflow.get("path") != WORKFLOW:
        return None
    sha = str(run.get("head_sha"))
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("invalid head SHA")
    pulls = [table(pull) for pull in array(run.get("pull_requests", []))]
    if not pulls:
        pulls = api.pages(f"{base}/commits/{sha}/pulls")
    matches = [
        integer(pull.get("number"))
        for pull in pulls
        if current_pull(api, repo, integer(pull.get("number")), sha)
    ]
    if len(matches) != 1:
        return None
    pull_number = matches[0]
    attempt = integer(run.get("run_attempt"))
    jobs = api.pages(f"{base}/actions/runs/{run_id}/attempts/{attempt}/jobs", "jobs")
    if not any(
        job.get("name") in {f"suites ({system})" for system in SYSTEMS}
        and job.get("conclusion") != "skipped"
        for job in jobs
    ):
        return None
    suites: list[Suite] = []
    selection: dict[str, object] = {}
    warnings: list[str] = []
    artifacts = api.pages(f"{base}/actions/runs/{run_id}/artifacts", "artifacts")
    for name in ["cli-bench-selection", *(f"cli-bench-{system}" for system in SYSTEMS)]:
        system = name.removeprefix("cli-bench-")
        matched = [artifact for artifact in artifacts if artifact.get("name") == name]
        artifact = max(matched, key=lambda item: integer(item.get("id")), default={})
        if not artifact or artifact.get("expired"):
            if name != "cli-bench-selection":
                warnings.append(f"{system}: artifact unavailable.")
            continue
        try:
            if name != "cli-bench-selection":
                job = next((job for job in jobs if job.get("name") == f"suites ({system})"), {})
                started, created = (
                    seconds(job.get("started_at")),
                    seconds(artifact.get("created_at")),
                )
                if (
                    started is None
                    or created is None
                    or created < started
                    or job.get("conclusion") == "skipped"
                ):
                    warnings.append(f"{system}: artifact unavailable for this attempt.")
                    continue
            integer(artifact.get("size_in_bytes"), MAX_ZIP)
            data = api.request(
                f"{base}/actions/artifacts/{integer(artifact.get('id'))}/zip", limit=MAX_ZIP
            )
            if name == "cli-bench-selection":
                selected = table(json.loads(read_zip(data)["selection.json"]))
                binaries = array(selected.get("binaries"))
                if len(binaries) > 64:
                    raise ValueError("too many selected binaries")
                selection = {
                    "binaries": [identifier(value) for value in binaries],
                    "runs": integer(selected.get("runs"), 1000),
                }
            else:
                suites.extend(read_suites(data, system))
        except (ValueError, KeyError, UnicodeError, zipfile.BadZipFile, RecursionError):
            warnings.append(f"{name}: invalid artifact data omitted.")
    markdown = render(repo, run, jobs, suites, selection, warnings)
    if author is not None:
        comments = api.pages(f"{base}/issues/{pull_number}/comments")
        owned = [
            comment
            for comment in comments
            if table(comment.get("user")).get("login") == author
            and MARKER.match(str(comment.get("body", "")))
        ]
        revision = (integer(run["run_number"]), attempt)
        for comment in owned:
            marker = MARKER.match(str(comment["body"]))
            assert marker is not None
            if (int(marker[1]), int(marker[3])) > revision:
                return markdown
        if not current_pull(api, repo, pull_number, sha):
            return None
        if owned:
            if owned[0].get("body") != markdown:
                api.request(
                    f"{base}/issues/comments/{integer(owned[0]['id'])}",
                    method="PATCH",
                    body={"body": markdown},
                )
        else:
            api.request(
                f"{base}/issues/{pull_number}/comments", method="POST", body={"body": markdown}
            )
    return markdown


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--post", action="store_true", help="Update the caller's PR comment")
    args = parser.parse_args()
    api = GitHub()
    author = None
    if args.post:
        author = (
            "github-actions[bot]"
            if os.environ.get("GITHUB_ACTIONS") == "true"
            else str(table(api.json("user"))["login"])
        )
    try:
        markdown = generate(api, args.repo, args.run_id, author=author)
        if markdown is None:
            print("Skipped: no current eligible PR benchmark run.")
            return
        if args.output:
            args.output.write_text(markdown, encoding="utf-8")
        if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
            with Path(summary).open("a", encoding="utf-8") as output:
                output.write(markdown)
        print(markdown, end="")
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        sys.exit(f"Cannot report benchmarks: {error}")


if __name__ == "__main__":
    main()
