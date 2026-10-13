# Testing

Run from the workspace root with the nightly pinned in
[rust-toolchain.toml](../rust-toolchain.toml). Install cargo-nextest to run tests;
nextest does not run doctests. CI validates Linux and macOS separately.

## Run tests

Start with the affected package. Replace `catr` with its Cargo package name:

```sh
cargo nextest run --locked -p catr
cargo test --locked -p catr --doc
```

Run doctests only for packages with a library target. For workspace validation,
use the all-feature commands from CI:

```sh
cargo nextest run --locked --workspace --all-features --profile ci
cargo test --locked --workspace --all-features --doc
```

The [CI profile](../.config/nextest.toml) disables retries and sets test and suite
timeouts. When changing shared APIs, include affected consumers and run the
standalone consumer:

```sh
cargo run --locked --manifest-path tests/consumers/Cargo.toml
```

## Write regressions

For a behavior fix, establish the baseline, add a failing regression, implement
the fix, and confirm it passes. Test observable results rather than implementation
details.

| Test level | What to verify |
| --- | --- |
| CLI integration | Arguments, stdin, exact stdout bytes, stderr, exit status, and filesystem effects. |
| Direct API | Changed library contracts, caller-owned I/O, returned errors, and partial failures. |
| Unit | Complex logic or boundary cases that are difficult to exercise through the CLI. |

Derive expectations from documented behavior, worked examples, or an independent
reference. Use deterministic inputs and temporary directories. Cover relevant
boundaries: empty input, missing final newlines, invalid UTF-8, numeric limits,
permissions, and multiple files. State platform constraints; Kara changes also
need interactive terminal checks.

Store reusable inputs in `tests/inputs/` and expected results in `tests/expected/`.
When regenerating fixtures with `mk-outs.nu`, inspect the package's script first,
record the reference executable and version, and review the output diff. Ordinary
tests must run without reference programs or Nushell.

## Check changes

The [PR workflow](../.github/workflows/pull-request-check.yml) owns the exact CI
commands, including formatting, Clippy, rustdoc, and feature checks. Local hooks
are defined in [prek.toml](../prek.toml); install prek and run `prek install` once
per clone to enable them.

Run checks affected by the change. Reuse passing results while their code,
fixtures, configuration, and environment remain unchanged. Report commands,
outcomes, and unverified platforms or missing tools; do not weaken checks to
obtain a pass.

## Documentation checks

For documentation-only changes, check local links, anchors, spelling, and whitespace:

```sh
prek run lychee --all-files --show-diff-on-failure
prek run typos --all-files --show-diff-on-failure
git diff --check
```

These system hooks require prek, lychee, and typos on PATH. Use `--files PATH`
instead of `--all-files` to check a new, untracked document. Rust checks are
unnecessary when only documentation changes.

For workflow edits, install actionlint and run:

```sh
prek run actionlint --all-files --show-diff-on-failure
```
