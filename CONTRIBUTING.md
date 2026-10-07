# Contributing

[Project overview](README.md) · [Agent guidance](AGENTS.md)

The [north star](docs/north-star.md) defines what each utility should achieve.
This guide owns the contribution workflow, check commands, and test conventions.
Implement one reviewable slice at a time; existing gaps are migration work.

## Change workflow

1. Read the app contract, source, tests, and relevant guide. Use the
   [utility template](docs/templates/utility.md) when establishing a contract.
   Reuse prior findings, rechecking changed files and unresolved claims. Record
   the baseline and exact behavior/API that should change; resolve product choices.
2. For compatibility work, identify the reference implementation/version and add
   a focused regression. Check option interactions, not just the new flag alone.
3. Implement the operation in the library and normalize CLI arguments at the edge.
   Exercise the public API without spawning the binary. Extract shared mechanisms
   only when actual consumers and their contracts are understood.
4. Run affected-package checks, then the workspace checks for Rust changes.
   Measure hot-path changes after correctness has been established. Update the
   app's compatibility notes, examples, and benchmark instructions together.
5. Open a PR describing what changed, what was verified, and what remains unknown.
   Keep unrelated defects visible as follow-up work rather than silently changing
   their scope or calling the whole workspace fixed.

## Checks

Run from the workspace root using the nightly toolchain selected by
[rust-toolchain.toml](rust-toolchain.toml). `rustfmt`, `clippy`, and `cargo-nextest`
are required for the complete set. The dated nightly is reproducible; it is not
an MSRV or stable-support declaration. Update its date in a reviewed change only
after the complete Linux and macOS checks pass, recording `rustc -Vv`.

Start with the affected package, using `catr` as an example:

```sh
cargo nextest run --locked -p catr
cargo test --locked -p catr --doc
cargo clippy --locked -p catr --all-targets --all-features -- -D warnings -F unsafe_code
```

The doctest command requires a library target. Then, for Rust changes:

```sh
cargo fmt --all -- --check
cargo clippy --locked --workspace --all-targets --all-features -- -D warnings -F unsafe_code
cargo nextest run --locked --workspace --profile ci
cargo test --locked --workspace --doc
```

The `ci` nextest profile disables retries, terminates a test after two minutes,
and limits a suite to ten minutes. JUnit output is in
`target/nextest/ci/junit.xml`. Local hooks select manifest, configuration and
fixture edits as well as Rust sources. Fmt may fix local formatting; CI checks
formatting first and rejects any tracked-file changes. First-party workspace
code forbids unsafe; dependencies are outside that guarantee.

These follow the intent of [prek.toml](prek.toml), adding a separate doctest step:
[nextest does not run doctests](https://nexte.st/docs/running/). Preserve existing
lint policy; do not hide failures with broad `allow` attributes. `--locked` avoids
incidental dependency resolution changes; deliberate dependency changes need their
own reviewed lockfile update. Test changed feature combinations as well as defaults
when a package actually declares features; do not invent unsupported feature flags.

A shared-crate change needs tests for its consumers, not only the helper itself.
Public API changes also need `cargo doc --locked -p catr --no-deps` for the affected
library and compilation of relevant examples. Existing failing checks must be
reported separately from introduced regressions.

For documentation-only PRs, review links, paths, shell syntax, examples, and
`git diff --check`. Compile changed Rust examples when possible. State explicitly
when tooling or a platform is unavailable; an unrun command is not a passing check.

## CI jobs

PRs and pushes to `master` run `quality`, native `tests (linux)` and
`tests (macos)`, `packages`, `dependencies`, `workflows`, `secrets` and `docs-policy`.
Compiler/test jobs deny warnings. `quality` checks formatting, Clippy and
workspace rustdoc; nextest and doctests run separately on each platform.
`packages` uses cargo-hack 0.6.45 to compile each member independently and check
its feature powerset, then runs full-feature nextest and doctests directly.
Mutually exclusive features need a documented valid-combination policy before
introduction. Required workflows have no path filters.

Workflow syntax/security checks use actionlint 1.7.12 and
[zizmor](https://github.com/zizmorcore/zizmor) 1.30.1. Install zizmor with
`cargo install zizmor --version 1.30.1 --locked`, then run
`prek run zizmor --all-files` or `zizmor --offline --no-progress .github/workflows`.
The local hook checks workflow edits; CI runs it as part of the required
`workflows` job. Commands live in the workflow and hook configuration.

`docs-policy` checks local Markdown links/anchors (lychee 0.24.2) and prose spelling
(typos 1.50.3). Cargo/Clippy enforce compiler lints; cargo-deny checks licenses.
Use `git ls-files -z -- '*.md' | xargs -0 lychee --offline --include-fragments=anchor-only --`
and `git ls-files -- '*.md' | typos --force-exclude --file-list -` locally.
Fixtures and generated data keep their intentional bytes; they are excluded from
spelling. Remote links run weekly with bounded retries and remain non-required.
The [guardrail evidence record](docs/ci-guardrails-status.md) distinguishes
implemented checks, verified runs and remaining limitations.

The `domain-clippy` hook selects `.config/domain-clippy` for biggie/tailr library
targets with `CLIPPY_CONF_DIR`. Its configuration prohibits global streams,
process arguments/environment and diagnostic initialization without applying
those domain restrictions to legitimate CLI adapters. Other workspace lints
still apply to all targets. `tests/consumers` is a separately locked workspace:
`cargo run --locked --manifest-path tests/consumers/Cargo.toml` proves repeated
API calls without CLI setup and without workspace feature unification.
Macro compile-fail snapshots use trybuild and the pinned compiler; review actual
diagnostics before accepting new snapshots during toolchain updates.

## Dependency policy

Use cargo-audit 0.22.2, cargo-deny 0.20.2 and cargo-machete 0.9.2. For each of
`Cargo.toml`, `tests/consumers/Cargo.toml` and `fuzz/Cargo.toml`, run
`cargo audit --file PATH/TO/Cargo.lock --deny yanked` and
`cargo deny --locked --manifest-path PATH/TO/Cargo.toml --config "$PWD/deny.toml" check licenses sources bans`.
Then run `cargo machete --with-metadata`. The required and scheduled workflows
show these commands directly and refresh RustSec into a fresh runner directory.
Vulnerabilities, yanked crates and network/database failures fail the check.
`deny.toml` permits the observed MIT, Apache-2.0 and Unicode-3.0 license options,
rejects unknown registries/Git sources, and reports duplicate versions for review.
Any exception needs an exact crate/version, reason and removal condition.
Scheduled audit also catches advisories when no PR is open.

The `master` ruleset requires PRs, current-base passing Actions checks and resolved
conversations, and prevents deletion/force-push. No additional approving reviewer
is required. Update required check names only after successful runs of the new
names, and read settings back; YAML does not itself enable merge protection.

## Scheduled investigations

`Maintenance` refreshes dependencies daily and checks remote links, coverage,
optimized-profile correctness, moving nightly/stable/beta and a pure-library Miri
subset weekly/manual. Stable/beta failures are readiness findings; Kara still
requires nightly. Miri runs parsu unit tests and tailr direct API tests, with
normal isolation. CodeQL analyzes Rust/Actions on PRs, master and weekly; scan
execution and alerts are reported before considering additional merge rules.

Coverage uses cargo-llvm-cov 0.9.1 and the pinned compiler's llvm-tools-preview:
create `target/coverage`, run `cargo llvm-cov nextest --locked --workspace --all-features --lcov --output-path target/coverage/lcov.info`, then
`cargo llvm-cov report --html --output-dir target/coverage`. Reports include
instrumented CLI subprocesses; doctests are tested separately and are not in
these coverage totals. There is no percentage threshold or third-party upload.

[Fuzz instructions](fuzz/README.md) describe direct cargo-fuzz commands for corpus
replay, short PR smoke and longer scheduled runs. Missing tools or failed probes
remain visible.

## Test conventions

Use this layout as packages migrate; do not create empty directories:

```text
tests/
  cli.rs        # process contract
  library.rs    # direct API integration tests, when useful
  inputs/       # small reusable read-only inputs
  expected/     # captured output, statuses, effects, provenance for ports
mk-outs.nu      # port reference-capture workflow; never called by ordinary tests
```

Keep unit tests near their operation and direct public-API integration tests in
`tests/library.rs` when needed. CLI tests use `assert_cmd::cargo::cargo_bin_cmd!`,
`predicates`, and small local helpers. For `bool`, select `cargo_bin_cmd!("true")`
or `cargo_bin_cmd!("false")` explicitly. Assert stdout bytes, stderr, status, and
filesystem effects, not only successful parsing.

Exercise option combinations, invalid direct-library options, state across calls
and operands, partial failure, and injected read/write/flush failures as relevant.
Use the contract's [text matrix](docs/north-star.md#u4--text-bytes-and-formatting)
to choose meaningful boundaries; do not treat decoding output as a substitute for
byte comparisons. Do not change old expectations without evidence for the intended
behavior change, weaken assertions, or ignore tests to hide a defect.

Use small inline cases or `tests/inputs/` and `tests/expected/` fixtures. Create
writable files with `assert_fs` or the existing `tempfile` helpers. Do not mutate
checkout fixtures or global cwd/environment; set child-process context instead.
Use fixed timestamps and locale/timezone settings for deterministic expectations,
with separate tests for locale-sensitive behavior.

Ordinary tests must not require reference installations, Nushell, network access,
privileged operations, or benchmark data generation. Reference comparisons are a
separate [reference-capture workflow](docs/compatibility.md#reference-and-regression-workflow).
Bound tests that can wait for input, follow files, or handle signals with timeouts
and reliable child-process cleanup.
Do not treat a platform-skipped test as compatibility evidence for that platform.

Existing examples: [catr](coreutils/catr/tests/cli.rs) for CLI I/O,
[pwdr](coreutils/pwdr/tests/cli.rs) for child environment isolation, and
[lsr](coreutils/lsr/tests/cli.rs) for temporary permission fixtures. These demonstrate
mechanisms, not certified GNU behavior. Kara changes also require checking affected
interactive behavior in a terminal.

## Documentation and PRs

Use the [utility template](docs/templates/utility.md) for app documentation; it links
the owning requirements rather than redefining them. Keep the root README small.
Record standards evidence and follow-ups without claiming a utility has migrated
merely because its README follows the template.

Use the [PR template](.github/pull_request_template.md). Performance-sensitive work
must link raw results or state why it was not measured. Compatibility claims must
name tested references. A future CI matrix, a planned API, or a benchmark target
is not an implemented capability merely because documentation describes it.

## References

The [AGENTS.md format](https://agents.md/) separates agent instructions from the
human-facing README. The [CLI Guidelines](https://clig.dev/#documentation) inform
examples and clear limitations, not changes that override reference behavior.
[Uutils' contributor guide](https://uutils.org/coreutils/docs/CONTRIBUTING.html)
provides a useful model for compatibility, testing, and shared utility code.
The architecture and benchmarking guides link more specific primary references.
