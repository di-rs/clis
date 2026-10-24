"""Render size measurements as data, using the shared trusted publication boundary."""

import zipfile

from githubkit import GitHub, TokenAuthStrategy
from report import MAX_ZIP, github_pages, github_request, integer, read_zip, seconds
from size_data import BinarySize, SizeReport

JOB = "📦 Measure Linux binary sizes"


def delta(before: int, after: int) -> str:
    difference = after - before
    percent = f"{difference / before:+.2%}" if before else "—"
    return f"{difference:+,} B | {percent}"


def row_text(row: BinarySize) -> str:
    before, after = row.base.bytes, row.head.bytes
    state = (
        "Unavailable"
        if "failed" in (row.base.status, row.head.status)
        else "Added"
        if row.base.status == "absent"
        else "Removed"
        if row.head.status == "absent"
        else "Unchanged"
        if before == after
        else "Changed"
    )
    change = "— | —" if state == "Unavailable" else delta(before or 0, after or 0)
    return (
        f"| `{row.package}` | `{row.binary}` | "
        f"{f'{before:,} B' if before is not None else '—'} | "
        f"{f'{after:,} B' if after is not None else '—'} | {change} | {state} |"
    )


def render_size(repo: str, run: dict[str, object], report: SizeReport | None) -> str:
    number, run_id, attempt = (integer(run[key]) for key in ("run_number", "id", "run_attempt"))
    lines = [
        f"<!-- cli-size-report number={number} run={run_id} attempt={attempt} -->",
        "### Package binary sizes",
        "",
        f"[Run {number}, attempt {attempt}](https://github.com/{repo}/actions/runs/{run_id}/attempts/{attempt})",
        "",
        "Linux release executable bytes · Advisory; size growth does not fail this check.",
        "",
    ]
    if report is None:
        return "\n".join(
            lines
            + ["> **Incomplete:** size artifact unavailable or invalid. Inspect the run logs.", ""]
        )
    lines += [
        f"[Merge base `{report.base_sha[:12]}`](https://github.com/{repo}/commit/{report.base_sha}) → "
        f"[PR `{report.head_sha[:12]}`](https://github.com/{repo}/commit/{report.head_sha})",
        "",
    ]
    complete = all(
        build.status != "failed" for row in report.rows for build in (row.base, row.head)
    )
    if not complete or run["conclusion"] != "success":
        lines += [
            "> **Incomplete:** some builds or workflow steps failed. Missing sizes are not zero; "
            "no complete total is available.",
            "",
        ]
    elif not report.rows:
        lines += ["No affected binary packages; no builds were needed.", ""]
    else:
        before = sum(row.base.bytes or 0 for row in report.rows)
        after = sum(row.head.bytes or 0 for row in report.rows)
        lines += [
            f"**Affected-set total:** {after:,} B (base: {before:,} B). "
            f"**Size change:** {delta(before, after).replace(' | ', ' · ')}.",
            "",
        ]
    changed = [
        row
        for row in report.rows
        if row.base.status != "complete"
        or row.head.status != "complete"
        or row.base.bytes != row.head.bytes
    ]
    unchanged = [row for row in report.rows if row not in changed]
    remaining = 120
    for title, rows, opened in (
        ("Changed binaries", changed, " open"),
        ("Unchanged binaries", unchanged, ""),
    ):
        visible = sorted(rows, key=lambda row: (row.package, row.binary))[:remaining]
        remaining -= len(visible)
        lines += [
            f"<details{opened}>",
            f"<summary><strong>{title}</strong> ({len(rows)})</summary>",
            "",
            "| Package | Binary | Base size | PR size | Change | Change % | Status |",
            "| --- | --- | ---: | ---: | ---: | ---: | --- |",
            *(row_text(row) for row in visible),
            "",
            "</details>",
            "",
        ]
    if len(report.rows) > 120:
        lines += [
            f"Showing 120 of {len(report.rows)} binaries; totals cover the full affected set. "
            "Full results are in the linked run artifacts.",
            "",
        ]
    lines += [
        "<details>",
        "<summary>Build details</summary>",
        "",
        f"Toolchain: `{report.toolchain}` · Target: `{report.target}` · Profile: `release` · Default features.",
        "",
        "Both revisions use the PR toolchain, locked dependencies, separate output directories, "
        "and one Cargo invocation per package. Sizes are the executable bytes as built; "
        "no additional stripping or compression is applied.",
        "",
        "</details>",
        "",
    ]
    return "\n".join(lines)


def generate_size(
    api: GitHub[TokenAuthStrategy], repo: str, run: dict[str, object], jobs: list[dict[str, object]]
) -> str | None:
    job = next((job for job in jobs if job.get("name") == JOB), None)
    if job is None or job.get("conclusion") == "skipped":
        return None
    run_id = integer(run["id"])
    artifacts = github_pages(api, f"repos/{repo}/actions/runs/{run_id}/artifacts", "artifacts")
    artifact = max(
        (item for item in artifacts if item.get("name") == "cli-size-linux"),
        key=lambda item: integer(item.get("id")),
        default={},
    )
    report = None
    try:
        start, created = seconds(job.get("started_at")), seconds(artifact.get("created_at"))
        if (
            not artifact
            or artifact.get("expired")
            or start is None
            or created is None
            or created < start
        ):
            raise ValueError("artifact unavailable for this attempt")
        integer(artifact.get("size_in_bytes"), MAX_ZIP)
        data = github_request(
            api, f"repos/{repo}/actions/artifacts/{integer(artifact.get('id'))}/zip", limit=MAX_ZIP
        )
        report = SizeReport.model_validate_json(read_zip(data)["sizes.json"])
        if report.head_sha != run["head_sha"] or report.run_attempt != run["run_attempt"]:
            raise ValueError("artifact revision mismatch")
        if report.target != "x86_64-unknown-linux-gnu":
            raise ValueError("unexpected build target")
    except ValueError, KeyError, UnicodeError, zipfile.BadZipFile, RecursionError:
        report = None
    return render_size(repo, run, report)
