# Bounded benchmark comment data

The 2026-10-07 scope replaces the former artifact envelope, export staging and
remote history publisher. No benchmark artifact upload, archive download,
`workflow_run` publisher, contents-write job or history branch is configured.
Local Rust history/export/replay and existing evidence remain unchanged. Remote
storage is deferred to [#12](https://github.com/di-rs/clis/issues/12).

## Measurement outputs

`summary.py` invokes the existing Rust `history -f json` projector on ephemeral
runner evidence, then selects already-computed observations. Python and the
comment job never recompute statistics or compare results from different runs.
The full local projection has a 16 MiB read limit and a 120-second command timeout;
it never crosses the job boundary. No export or copy-for-upload operation runs.

Each independently named Linux/macOS smoke/full job emits one single-line JSON
output, at most **24,000 UTF-8 bytes**. Newlines in data are JSON escapes, not
workflow-output delimiters. The top-level fields are exactly:

```json
{"version":1,"platform":"linux","profile":"full","suites":[]}
```

`suites` contains exactly the selected package subset, without duplicates. Each
suite has `package`, `outcome`, nullable `failure`, `cases`, `omitted_cases`, and
`issues`. Packages are `biggie`, `tailr`, and `mkdirr`; outcomes are `complete`,
`failed`, `incomplete`, or `unavailable`. A missing/failed projection is explicit.
At most 12 cases and four analysis issue strings are projected per suite. Labels
and failure text are shortened to 128 Unicode characters with a visible ellipsis.
Further case rows may be dropped to meet the aggregate output bound; their count
is added to `omitted_cases`. A suite may contain at most 128 total cases including
omissions. Every suite outcome remains present.

Each case contains `id`, at most three distinct `roles`, and at most two distinct
`comparisons`. A role contains its allowlisted name, nullable elapsed mean seconds,
nullable RSS mean bytes, and integral executable bytes. A comparison contains
baseline (`previous` or `reference`), candidate (`candidate`), nullable ratio,
nullable elapsed-change percent, and the Rust direction enum. Numbers must be
finite, bounded by absolute 10^15, and nonnegative except elapsed-change percent.
Failed outcomes cannot carry ratios or directional conclusions. Smoke directions
are `smoke-only` or `unavailable`. The inline consumer validates exact fields,
counts, types, package/profile/OS identity and byte bounds independently.

## Comment identity and retained results

A checkout-free job in the **same workflow** uses pinned `actions/github-script`
with only `actions: read` and `pull-requests: write`. Outputs enter through
environment variables, never script interpolation. It verifies current run,
repository, PR association, exact current PR head and run/attempt ordering from
GitHub's API. It updates only the `github-actions[bot]` comment beginning with
`<!-- cli-bench:v1 -->`; human/other-bot comments are preserved. Multiple owned
markers, malformed metadata or unavailable source proof fail without mutation.
Fork PR benchmark jobs are skipped; Dependabot has no comment fallback.

The bot comment is the sole bounded retained state. Its hidden `cli-bench-run`
header identifies the updating run/attempt/head. One hidden `cli-bench-state`
field stores canonical base64 JSON, version 1, with at most **12 slots**: one per
package, OS and profile. Each slot has exactly `platform`, `profile`, `run_id`,
`attempt`, `sha`, `previous_sha`, `status`, and the compact `suite` above. Decoded
metadata is capped at **22 KiB (22,528 bytes)**; base64 overhead is included in the
whole-comment **60,000 UTF-8 byte** cap. Every retained source's repository,
workflow, PR association and measured SHA is API-verified, with cached reads for
up to 12 distinct retained source runs. Source links use API-derived run URLs.

A selected CLI replaces all its OS/profile slots, even when setup, tests or
measurement fail or an output is missing. Skipped CLIs retain their last compact
results, visibly labelled with the original measured commit, baseline and run.
Docs-only pushes add a no-benchmarks note; they do not relabel historical results
as measurements of the new head. Selection failure is explicit and preserves
labelled prior results. Full observations appear before smoke; smoke is wiring-only.

If the final metadata/comment exceeds its bounds, only case rows are removed and
omission counts increase. All statuses and source identities survive. All labels
are escaped into inert text, including Markdown punctuation, HTML and mentions.
No existing rendered Markdown, arbitrary URL, command or downloaded executable is
consumed. Source links lead to workflow status/logs; there is no full-evidence
artifact link and raw observations are not recoverable from a truncated comment.

Workflow concurrency serializes PR writers. The head is reread immediately before
mutation, and older run/attempt ordering is checked against API-proven metadata.
GitHub has no atomic PR-head/comment compare-and-set: the final API interval cannot
be eliminated. API calls use fixed endpoints, 10-second request timeouts, no
retries, at most 32 requests and 10 comment pages of 50 entries. Responses exceeding
4 MiB after SDK decoding are rejected. The SDK buffers API responses before this
check; the endpoint/page bounds and job deadline also constrain resource use.
Errors use fixed diagnostics without echoing data, exception headers or tokens.

## Tests and limits

`test_summary.py` exercises the compact projection and no-export path.
`test_impact.py` uses original temporary Git graphs for source/dependency/range
selection. `test_native.py` executes the real shell adapter with an explicit test
CLI to verify failure continuation and empty-selection behavior.
`test_comment.js` executes the exact inline workflow script with an injected
GitHub API, including no-write abuse cases, stale-head races, retained source
proof, partial reruns, missing OS results and maximum comment bounds. There is no
separate Node project or dependency installation.

The compact interface cannot prove numerical integrity of candidate-produced
outputs independently: measurement jobs run same-repository candidate code. The
Rust projector owns evidence validation/statistics; the comment job enforces the
small display/identity boundary. Raw full evidence stays local/ephemeral. Local
checks do not prove a hosted workflow or actual comment update has occurred.

Direct `cli-bench.yml` PR runs own the benchmark comment. Reusable calls under
another workflow run tests/measurements with read-only permissions and skip the
comment job; the workflow-reference guard and API workflow-path check do not
grant caller workflows publication authority.
