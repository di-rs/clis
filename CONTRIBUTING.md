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

Use this layout as packages migrate; do not create empty directories:

```text
src/
  <operation>.rs  # implementation and its #[cfg(test)] mod tests
tests/
  cli.rs          # CLI process integration tests
  library.rs      # public API integration tests, when useful; never unit tests
  process.rs      # subprocess lifecycle integration tests, when needed
  common/mod.rs   # shared integration-test helpers, when needed; no test cases
  inputs/         # small reusable read-only inputs
  expected/       # captured output, statuses, effects, provenance for ports
mk-outs.nu        # port reference-capture workflow; never called by ordinary tests
```

Rust unit tests must live in a `#[cfg(test)] mod tests` in the same source file as
the code they test. This applies to parsers, calculations, validators, I/O helpers,
and other implementation details. `src/lib.rs` may contain unit tests for code
implemented there; it must not collect unit tests for other modules.

`tests/` is for integration tests and their support files only. Use
`tests/library.rs` to exercise the public API as an external consumer, including
workflows across components; do not place isolated function/unit tests there just
because the function is public. Integration tests import the package's public API;
do not include source modules with `#[path]` or expose internals solely to test them.
Keep unit-test helpers beside their unit tests; `tests/common/mod.rs` shares helpers
between integration tests. Do not duplicate unit coverage in integration tests.

CLI tests use `assert_cmd::cargo::cargo_bin_cmd!`,
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
