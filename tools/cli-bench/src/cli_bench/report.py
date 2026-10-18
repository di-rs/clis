#!/usr/bin/env python3.15
"""Render PR benchmark artifacts as data; optionally update the caller's sticky comment."""

import argparse
import io
import json
import os
import re
import subprocess
import sys
import tomllib
import zipfile
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import datetime
from itertools import groupby
from pathlib import Path, PurePosixPath
from typing import cast

import anyio
import httpx
from githubkit import BaseAuthStrategy, GitHub, TokenAuthStrategy
from githubkit.exception import GitHubException
from pydantic import BaseModel, ConfigDict, Field

MAX_JSON = 2 * 1024 * 1024
MAX_PAGES = 20


class PaginationLimit(ValueError):
    """A complete listing cannot be established within the repository's limit."""


def table(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return cast(dict[str, object], value)


def array(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("expected JSON array")
    return cast(list[object], value)


def github_client() -> GitHub[TokenAuthStrategy]:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("Set GH_TOKEN or GITHUB_TOKEN for GitHub publication")
    return GitHub(token, auto_retry=False, http_cache=False, timeout=60)


def github_request[A: BaseAuthStrategy](
    api: GitHub[A],
    path: str,
    *,
    method: str = "GET",
    body: object = None,
    limit: int = MAX_JSON,
    timeout: float = 60,
) -> bytes:
    # SDK error and redirect handling also reads bodies. Bound the underlying
    # stream before that handling, as well as the decoded successful response.
    async def bound_response(response: httpx.Response) -> None:
        # GitHubKit buffers error bodies before returning them. Request identity
        # encoding and reject compressed responses before its decoder can inflate
        # an otherwise small wire body beyond the repository's byte budget.
        if response.headers.get("content-encoding", "identity").strip().lower() not in {
            "",
            "identity",
        }:
            raise ValueError("GitHub response uses unexpected HTTP compression")
        upstream = response.stream
        if not isinstance(upstream, httpx.AsyncByteStream):
            raise ValueError("expected an asynchronous GitHub response")
        stream: httpx.AsyncByteStream = upstream

        class LimitedBody(httpx.AsyncByteStream):
            async def __aiter__(self) -> AsyncIterator[bytes]:
                size = 0
                try:
                    async for chunk in stream:
                        size += len(chunk)
                        if size > limit:
                            raise ValueError("GitHub response exceeds size limit")
                        yield chunk
                finally:
                    with anyio.CancelScope(shield=True):
                        await stream.aclose()

            async def aclose(self) -> None:
                with anyio.CancelScope(shield=True):
                    await stream.aclose()

        response.stream = LimitedBody()

    async def fetch() -> bytes:
        config = replace(
            api.config,
            http_cache=False,
            auto_retry=None,
            timeout=httpx.Timeout(timeout),
            async_event_hooks={"response": [bound_response]},
        )
        async with GitHub(api.auth, config=config) as client:
            with anyio.fail_after(timeout):
                response = await client.arequest(
                    method, path, json=body, stream=True, headers={"Accept-Encoding": "identity"}
                )
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(data) + len(chunk) > limit:
                        raise ValueError("GitHub response exceeds size limit")
                    data.extend(chunk)
                return bytes(data)

    try:
        return anyio.run(fetch)
    except (GitHubException, httpx.HTTPError, TimeoutError) as error:
        # SDK error strings can contain response bodies or signed artifact URLs.
        raise ValueError(
            "GitHub request failed or exceeded its response limit or deadline"
        ) from error


def github_json[A: BaseAuthStrategy](
    api: GitHub[A],
    path: str,
    *,
    method: str = "GET",
    body: object = None,
) -> object:
    return cast(object, json.loads(github_request(api, path, method=method, body=body)))


def github_pages[A: BaseAuthStrategy](
    api: GitHub[A],
    path: str,
    key: str | None = None,
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    separator = "&" if "?" in path else "?"
    for page in range(1, MAX_PAGES + 1):
        response = github_json(api, f"{path}{separator}per_page=100&page={page}")
        values = array(table(response)[key] if key else response)
        result.extend(table(value) for value in values)
        if len(values) < 100:
            return result
    raise PaginationLimit("GitHub pagination limit exceeded")


MAX_ZIP = 8 * 1024 * 1024
MAX_MEMBER = 1024 * 1024
SYSTEMS = ("ubuntu-24.04", "macos-15")
PLATFORMS = {"ubuntu-24.04": "Linux", "macos-15": "macOS"}
WORKFLOW = ".github/workflows/cli-bench.yml"
MARKER = re.compile(r"<!-- cli-bench-report number=(\d+) run=(\d+) attempt=(\d+) -->")


def integer(value: object, maximum: int = 10**18, minimum: int = 1) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("invalid integer")
    return value


def identifier(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", value):
        raise ValueError("invalid identifier")
    return value


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


class ReportRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)


class Measurement(ReportRecord):
    system: str
    binary: str
    case: str
    candidate: float = Field(gt=0, allow_inf_nan=False)
    comparison: float | None = Field(allow_inf_nan=False)
    kind: str


class Suite(ReportRecord):
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
                    system=system,
                    binary=binary,
                    case=case,
                    candidate=mean(results[-1], runs),
                    comparison=comparison,
                    kind=str(names[0]),
                )
            )
        suites.append(Suite(binary=binary, status=str(status), runs=runs, rows=rows))
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


def result_label(value: object) -> str:
    status = identifier(value)
    return {"success": "Passed", "failure": "Failed"}.get(status, status.replace("_", " ").title())


def measurement_tables(rows: list[Measurement]) -> list[str]:
    ordered = sorted(rows, key=lambda row: (row.binary, row.case, SYSTEMS.index(row.system)))
    lines = ["#### Measurements", "", "Mean wall-clock time; lower is better."]
    if any(row.comparison is not None for row in rows):
        lines.append("Relative time is this PR ÷ comparison; below 1× means less wall time.")
    for binary, group in groupby(ordered[:200], key=lambda row: row.binary):
        cases = list(group)
        count = len({row.case for row in cases})
        platforms = ", ".join(
            PLATFORMS[system] for system in SYSTEMS if any(row.system == system for row in cases)
        )
        kinds = {row.kind for row in cases if row.comparison is not None}
        comparison_label = (
            "System"
            if kinds == {"reference"}
            else "Previous version"
            if kinds == {"baseline"}
            else "Comparison"
        )
        lines += [
            "",
            "<details>",
            f"<summary><strong>{binary}</strong> · {count} case{'s' if count != 1 else ''} · {platforms}</summary>",
            "",
            "| Case | Platform | This PR |"
            + (f" {comparison_label} | Relative time |" if kinds else ""),
            "| --- | --- | ---: |" + (" ---: | ---: |" if kinds else ""),
        ]
        for row in cases:
            line = f"| `{row.case}` | {PLATFORMS[row.system]} | {row.candidate * 1000:.3f} ms |"
            if kinds:
                comparison = (
                    f"{row.comparison * 1000:.3f} ms" if row.comparison is not None else "—"
                )
                if row.comparison is not None and len(kinds) > 1:
                    comparison += " (system)" if row.kind == "reference" else " (previous version)"
                ratio = (
                    f"{row.candidate / row.comparison:.2f}×" if row.comparison is not None else "—"
                )
                line += f" {comparison} | {ratio} |"
            lines.append(line)
        lines += ["", "</details>"]
    if len(rows) > 200:
        lines += [
            "",
            f"Showing the first 200 of {len(rows)} measurements; full results are in the run artifacts.",
        ]
    return lines


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
        "**Smoke check:** 1 measured run per command. These samples check execution; "
        "they do not establish statistically significant performance changes."
        if counts == {1}
        else "**Requested benchmark:** 20 measured runs per command. "
        "Relative times are descriptive; no statistical regression test was performed."
        if counts == {20}
        else "Measurement count unavailable or inconsistent."
    )
    status = result_label(run["conclusion"])
    lines = [
        f"<!-- cli-bench-report number={number} run={run_id} attempt={attempt} -->",
        "### CLI benchmarks",
        "",
        f"**{status}** · `{sha[:12]}` · "
        f"[Run {number}, attempt {attempt}](https://github.com/{repo}/actions/runs/{run_id}/attempts/{attempt})",
        "",
        "**Selected CLIs:** " + (", ".join(f"`{name}`" for name in binaries) or "unavailable"),
        "",
        f"> {mode}",
        "",
        "#### CI time",
        "",
        "| Platform | Total job | Build | Benchmarks | Result |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for system in SYSTEMS:
        job = next(
            (item for item in jobs if item.get("name") == f"⏱️ Run CLI benchmarks ({system})"), None
        )
        if job is None:
            lines.append(f"| {PLATFORMS[system]} | — | — | — | Not run |")
            continue
        steps = [table(value) for value in array(job.get("steps", []))]
        times = [
            next((clock(duration(step)) for step in steps if step.get("name") == name), "—")
            for name in ("🏗️ Build selected CLI executables", "⏱️ Execute selected benchmark suites")
        ]
        lines.append(
            f"| {PLATFORMS[system]} | {clock(duration(job))} | {times[0]} | {times[1]} | "
            f"{result_label(job.get('conclusion'))} |"
        )
    starts = [value for job in jobs if (value := seconds(job.get("started_at"))) is not None]
    ends = [value for job in jobs if (value := seconds(job.get("completed_at"))) is not None]
    elapsed = max(ends) - min(starts) if starts and ends else 0
    lines += [
        "",
        f"**{clock(elapsed)} elapsed** · "
        f"**{clock(sum(duration(job) for job in jobs))} summed runner time**",
        "",
        "Elapsed spans the first job start to the last finish. Runner time adds parallel jobs "
        "and selection, excluding queue time. Platforms: Ubuntu 24.04 and macOS 15.",
        "",
    ]
    rows = [row for suite in suites for row in suite.rows]
    if rows:
        lines += measurement_tables(rows)
    else:
        lines.append("No completed benchmark measurements were available.")
    if any(suite.status != "complete" for suite in suites):
        warnings.append("Some suites did not complete; only completed cases are shown.")
    lines.extend(["", *warnings])
    return "\n".join(lines).rstrip() + "\n"


def current_pull(api: GitHub[TokenAuthStrategy], repo: str, number: int, sha: str) -> bool:
    pull = table(github_json(api, f"repos/{repo}/pulls/{number}"))
    base = table(pull.get("base"))
    return (
        pull.get("state") == "open"
        and table(pull.get("head")).get("sha") == sha
        and table(base.get("repo")).get("full_name") == repo
        and base.get("ref") == "master"
    )


def generate(
    api: GitHub[TokenAuthStrategy], repo: str, run_id: int, *, author: str | None = None
) -> str | None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("invalid repository")
    base = f"repos/{repo}"
    run = table(github_json(api, f"{base}/actions/runs/{integer(run_id)}"))
    if (
        run.get("id") != run_id
        or run.get("event") != "pull_request"
        or run.get("status") != "completed"
        or run.get("conclusion") in ("cancelled", "skipped")
        or run.get("path") != WORKFLOW
        or run.get("name") != "⏱️ CLI benchmark execution"
        or table(run.get("repository")).get("full_name") != repo
    ):
        return None
    workflow = table(github_json(api, f"{base}/actions/workflows/cli-bench.yml"))
    if workflow.get("id") != run.get("workflow_id") or workflow.get("path") != WORKFLOW:
        return None
    sha = str(run.get("head_sha"))
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("invalid head SHA")
    pulls = [table(pull) for pull in array(run.get("pull_requests", []))]
    if not pulls:
        pulls = github_pages(api, f"{base}/commits/{sha}/pulls")
    matches = [
        integer(pull.get("number"))
        for pull in pulls
        if current_pull(api, repo, integer(pull.get("number")), sha)
    ]
    if len(matches) != 1:
        return None
    pull_number = matches[0]
    attempt = integer(run.get("run_attempt"))
    jobs = github_pages(api, f"{base}/actions/runs/{run_id}/attempts/{attempt}/jobs", "jobs")
    if not any(
        job.get("name") in {f"⏱️ Run CLI benchmarks ({system})" for system in SYSTEMS}
        and job.get("conclusion") != "skipped"
        for job in jobs
    ):
        return None
    suites: list[Suite] = []
    selection: dict[str, object] = {}
    warnings: list[str] = []
    artifacts = github_pages(api, f"{base}/actions/runs/{run_id}/artifacts", "artifacts")
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
                job = next(
                    (job for job in jobs if job.get("name") == f"⏱️ Run CLI benchmarks ({system})"),
                    {},
                )
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
            data = github_request(
                api, f"{base}/actions/artifacts/{integer(artifact.get('id'))}/zip", limit=MAX_ZIP
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
        except ValueError, KeyError, UnicodeError, zipfile.BadZipFile, RecursionError:
            warnings.append(f"{name}: invalid artifact data omitted.")
    markdown = render(repo, run, jobs, suites, selection, warnings)
    if author is not None:
        comments = github_pages(api, f"{base}/issues/{pull_number}/comments")
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
                github_request(
                    api,
                    f"{base}/issues/comments/{integer(owned[0]['id'])}",
                    method="PATCH",
                    body={"body": markdown},
                )
        else:
            github_request(
                api, f"{base}/issues/{pull_number}/comments", method="POST", body={"body": markdown}
            )
    return markdown


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--post", action="store_true", help="Update the caller's PR comment")
    args = parser.parse_args()
    try:
        api = github_client()
        author = None
        if args.post:
            author = (
                "github-actions[bot]"
                if os.environ.get("GITHUB_ACTIONS") == "true"
                else str(table(github_json(api, "user"))["login"])
            )
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
