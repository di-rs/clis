# Contributing

[Project overview](README.md) · [Agent guidance](AGENTS.md)

Prioritize the existing utilities: verified GNU/BSD behavior, useful library APIs,
and measured performance. Implement one reviewable slice at a time rather than
combining a workspace rewrite with a large flag expansion.

## Change workflow

1. Read the affected app README, source, tests, and relevant engineering guide.
   Record the current baseline and exact behavior or API that should change.
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
are required for the complete set. Record the actual toolchain version because
`nightly` is currently unpinned.

Start with the affected package, using `catr` as an example:

```sh
cargo nextest run --locked -p catr
cargo test --locked -p catr --doc
cargo clippy --locked -p catr --all-targets --all-features
```

The doctest command requires a library target. Then, for Rust changes:

```sh
cargo fmt --all -- --check
cargo clippy --locked --workspace --all-targets --all-features
cargo nextest run --locked --workspace
cargo test --locked --workspace --doc
```

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

## Test conventions

Keep CLI tests in each package's `tests/cli.rs`, using `assert_cmd`, `predicates`,
and small local helpers. Assert stdout bytes, stderr, status, and side effects as
applicable. For the `bool` package, select the `true` or `false` binary explicitly.
Keep direct library tests near the operation or in a dedicated integration test.

Use small inline cases or `tests/inputs/` and `tests/expected/` fixtures. Create
writable files with `assert_fs` or the existing `tempfile` helpers. Do not mutate
checkout fixtures or global cwd/environment; set child-process context instead.
Use fixed timestamps and locale/timezone settings for deterministic expectations,
with separate tests for locale-sensitive behavior.

Ordinary tests must not require GNU/BSD installations, Nushell, network access,
privileged operations, or benchmark data generation. Reference comparisons are a
separate workflow. When using `mk-outs.nu`, record reference versions and inspect
all fixture diffs; never regenerate expectations just to make failures disappear.
Use temporary sandboxes and timeouts for destructive commands or follow-mode tests.
Do not treat a platform-skipped test as compatibility evidence for that platform.

## Documentation and PRs

App READMEs should contain a purpose, implemented capabilities, explicit working
directory, CLI examples, a library example where available, reference manuals,
known gaps, and benchmark reproduction links when present. Link back to the root
README. Mark migration targets and unverified behavior clearly; preserve credits.

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
