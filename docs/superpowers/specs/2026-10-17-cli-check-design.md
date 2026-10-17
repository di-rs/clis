# CLI standards checks and GitHub SDK migration

## Outcome

Contributors should have one command that reports whether the repository's Rust
CLI packages follow its structural standards. Replace `tools/cli-testing` with
`tools/cli-check`, preserving `utils/cli-testing` as the Rust integration-test
library. Consolidate the package rules currently scattered across test policy,
playground discovery and benchmark selection.

Remove `tools/ci` by replacing its custom GitHub transport with GitHubKit in the
two publishing tools. Do not introduce a replacement shared GitHub package or
move Python code into `utils/`. The user approved the focused standards harness
and asked whether an existing SDK could replace the helper.

This is a structural validation tool. Test execution, assertion quality,
documentation quality and performance measurements retain their existing owners.

## Decisions

- Publish a local Python workspace package named `cli-check`, with import name
  `cli_check` and one console entry point named `cli-check`.
- Keep four check groups: `layout`, `tests`, `docs` and `benches`.
- Use one manifest-based workspace inventory for the checker, benchmark selector
  and playground CLI discovery. The checker needs no Cargo executable or network.
- Keep benchmark execution and reporting in `tools/cli-bench`.
- Use GitHubKit directly in the publishers. Keep repository-specific publication
  rules in those publishers, without creating another generic transport class.
- Use the root uv workspace, Python version pin, lockfile and shared checks.

## Command and diagnostics

From the repository root:

```sh
uv run --locked cli-check
uv run --locked cli-check layout
uv run --locked cli-check tests
uv run --locked cli-check docs
uv run --locked cli-check benches
uv run --locked cli-check --root /path/to/workspace
```

No group means all groups. `--root` is available with each group and selects a
different workspace for regression tests and explicit inspection. Root discovery
starts from the current directory and searches parents for the Cargo workspace;
an explicit root must contain the workspace manifest. Running outside a workspace
without `--root` produces an actionable error.

Report every discovered violation, sorted by repository-relative path and rule
identifier. Each diagnostic includes the path, group, stable rule identifier and
required correction. Exit 0 means the selected checks passed, 1 means violations
or unreadable/invalid repository data, and 2 means invalid command-line usage.
Repository errors should not produce Python tracebacks.

The command reads files only. It does not update fixtures, rewrite manifests,
execute generators, build packages, run CLI tests or execute benchmark cases.

## Package structure and shared inventory

Use `tools/cli-check/src/cli_check/` for the command, workspace inventory,
diagnostics and focused check modules. Keep its README, pyproject and subprocess
regressions under the same tool directory. Expose a small typed inventory API for
the two existing discovery consumers; do not make them parse command output.

Inventory expands the root workspace's declared `members` and `exclude` patterns
and includes a root package when present. Preserve the existing documented scope:
implicit members introduced only by path dependencies must be declared in the
root workspace, and Cargo validates the complete build graph separately.

Record package name, directory, manifest, library presence and declared or
automatically discovered binary targets. Respect `autobins` and explicit target
overrides. Identify malformed CLI layouts, including explicit binaries outside
`src/main.rs` and packages using only `src/bin/`, before applying the layout rule;
do not silently omit them. A library can coexist with its package's one binary.
Examples, tests and Cargo benchmark targets are not CLI binaries.

The playground retains its own name restrictions, package-count bounds and
selection logic. Benchmark selection retains PR diff and request handling.
Both consume the same inventory instead of maintaining competing interpretations
of workspace membership and CLI targets.

## Standards checked

### Layout

Every CLI package must define exactly one effective binary target. Package name,
directory name and binary name must match, and its source must be `src/main.rs`.
Reject additional binaries and CLI source files under `src/bin/`. A single
explicit `[[bin]]` describing the matching binary remains allowed. Pure library
packages do not need a binary and do not receive CLI-only checks.

### Tests

Preserve all existing policy behavior: a nonempty `tests/cli.rs` must be enabled
as a Cargo test target, `cli-testing` must be a development dependency, the legacy
`mk-outs.nu` filename is rejected, and `mk-outputs.nu` requires `tests/expected/`.
Keep coverage for `autotests = false`, explicit test paths and `test = false`.
Do not require fixture directories for CLIs without fixtures. Do not claim that
manifest registration establishes feature eligibility or behavioral coverage.

### Docs

Each CLI needs a nonempty package-local `README.md` and a link to that README
from the root README. Check local file targets in those READMEs, including image
targets and reference links. Resolve links relative to their containing document;
ignore external schemes and fragment-only links. For links with a file and a
fragment, validate the file path only. General anchor, external link, spelling
and Rust API documentation checks retain their existing tools.

Use MarkdownIt from `markdown-it-py` to extract actual Markdown links, including
reference links, while ignoring code blocks. Declare it as a `cli-check`
dependency instead of implementing a partial Markdown parser.

### Benches

Each CLI needs at least one TOML suite under its own `benches/`, unless it has a
documented exemption. Every suite's `binary` must match the owning package. It
must contain at least one case, with a nonempty unique name and argument array.
The benchmark runner keeps its complete execution-schema validation.

Represent the exception in its owning manifest:

```toml
[package.metadata.cli-check]
benchmark-exemption = "Interactive terminal editor; no finite command benchmark."
```

Add this metadata to Kara, matching its existing documented benchmark scope.
Require a nonempty explanation. Reject an exemption that coexists with a suite
so that obsolete exemptions do not silently persist. Do not infer exemptions
from package names or accept an empty `benches/` directory as coverage.

## GitHub SDK decision

GitHubKit is the selected SDK. Its documented API provides synchronous and
asynchronous requests, typed endpoint methods, pagination, streaming responses
and injectable HTTPX transports. Its HTTPX, AnyIO and Pydantic dependencies fit
the repository's existing Python stack. PyGithub is a viable alternative, but
GitHubKit's documented streaming and transport injection fit these publishers
more directly. This is a suitability assessment, not completed Python 3.15
runtime validation.

Delete `tools/ci` after migrating `tools/cli-bench/report.py` and
`tools/cli-playground/preview_ci.py`. Inject an SDK client into publication code
and use generated endpoint methods where appropriate. Repository-owned artifact
formats still need their existing data validation; moving that validation into
an SDK-shaped shared library would not simplify the design.

Preserve the existing policies: bounded pages, JSON and artifact response sizes,
elapsed request limits, current PR/head/workflow validation, artifact provenance,
safe ZIP handling, comment ownership and stale-publication rejection. Disable
SDK caching for authoritative state reads and disable automatic retries so they
cannot extend bounded operations or repeat writes unexpectedly.

Use the SDK's asynchronous requests under cancellable AnyIO deadlines where
needed to retain the existing whole-request limit, including a continuously
trickling response. A socket inactivity timeout alone is insufficient. Stream
responses and enforce byte limits before buffering the complete body. Bound
pagination and fail on exhaustion rather than treating a truncated listing as
complete. Close streamed responses and SDK clients on every exit path. Keep
these policies local to each publisher; do not implement an HTTP transport.

Authenticate from the already supplied `GH_TOKEN`, falling back to
`GITHUB_TOKEN` for explicit local use. Never print credentials. Test artifact
redirect behavior, including that credentials do not reach a different origin.
CLI commands no longer require `gh` for these API operations.

Pin a compatible SDK minor series in project requirements and its exact resolved
version in `uv.lock`, since GitHubKit documents breaking changes in minor releases.
Runtime tests on the repository's Python 3.15 pin must establish compatibility.

## Integration and migration

Replace the old uv workspace member and register `cli-check` as an installable
package with the repository's existing uv build backend. Add `cli-check`,
GitHubKit and directly used HTTPX/AnyIO dependencies to the root tooling project's
runtime dependencies. This keeps them available to existing `uv run --no-dev`
publisher commands without adding them to deployed service dependencies. Register
`cli-check` as a workspace source and update the committed root lockfile.

Replace the `cli-test-policy` prek hook with `cli-check` and run the full harness
from the Python CI job. Include manifests, Rust targets, tests, fixture generator
names, READMEs, benchmark TOML, checker sources and relevant root configuration
in its trigger paths. CI's all-files invocation must still catch deleted files.

Update the testing skill's checker instructions and links, the testing and
benchmarking guides, root tool documentation and pytest paths. Remove references
to `tools/cli-testing`, the old hook and `tools/ci`. References to the Rust
`cli-testing` library and the CLI testing skill retain their existing names.

Do not add benchmark execution to the harness, pushes or merges. Preserve
affected-suite selection and trusted default-branch publication boundaries.

## Verification and acceptance

Exercise the real checker command against temporary workspace trees. Cover
passing workspaces, each failed rule, multiple simultaneous errors, group
selection, explicit roots, root packages, members/excludes, libraries, target
autodiscovery and overrides, broken/reference Markdown links, missing suites,
wrong suite owners and benchmark exemptions. Retain the existing test-policy
regressions and run the complete harness on the real repository.

Adapt publisher regressions to SDK clients with HTTPX mock transports. Cover
their existing publication contracts, pagination filters and exhaustion, response
size limits, total deadlines, resource cleanup and redirected downloads. Add a
local HTTP integration case when a mock cannot establish actual streaming or
timeout behavior. Tests do not publish comments, deployments or previews.

Run locked root Ruff lint and format checks, strict mypy, affected Python tests,
then the root Python suite. Check changed Markdown with lychee and typos, changed
workflows with actionlint, and run `git diff --check`. Verify production-style
`--no-dev` dependency resolution and tool invocation. No Rust implementation
changes or timed benchmark runs are required for this tooling refactor.

Completion means the new command passes on the repository, all four groups have
behavioral regression coverage, discovery consumers share the inventory, both
publishers use the SDK, and neither retired directory nor a replacement GitHub
helper package remains.

## SDK sources

- [GitHubKit REST API: endpoint methods, streaming and pagination](https://yanyongyu.github.io/githubkit/usage/rest-api/)
- [GitHubKit configuration: timeout, transports and caching](https://yanyongyu.github.io/githubkit/usage/getting-started/configuration/)
- [GitHubKit retry configuration](https://yanyongyu.github.io/githubkit/usage/auto-retry/)
- [GitHubKit test transports](https://yanyongyu.github.io/githubkit/usage/unit-test/)
- [GitHubKit versioning policy](https://yanyongyu.github.io/githubkit/)
- [PyGithub client configuration](https://pygithub.readthedocs.io/en/stable/github.html)
