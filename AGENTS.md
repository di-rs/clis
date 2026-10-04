# Repository guidance

## Layout and scope

- See the [README](README.md) for the app index, workspace overview, and prerequisites.
- Most apps use `src/cli.rs` for Clap arguments, `src/main.rs` for I/O and exit
  handling, and `src/lib.rs` for reusable logic. Preserve simpler layouts where used.
- Keep changes local. Do not add a shared framework, dependencies, or full GNU
  compatibility unless the task needs them. Do not modify Kara for unrelated CLI work.
- Reuse workspace dependencies and lints. Error handling varies (`thiserror`,
  `color-eyre`, explicit exit codes); follow the app rather than rewriting it.

## Checks

Use the commands in [README → Development](README.md#development). Run
affected-package checks first, then the workspace suite. Check formatting and
Clippy as configured in `prek.toml`.

- Check a baseline before editing; distinguish existing failures from regressions.
  Keep the regression suite passing when changing supported behavior.
- Assert intended behavior. Do not weaken or ignore tests to hide a defect; report
  failures and keep production fixes within the approved scope.
- When changing Kara, also verify affected interactive behavior in a terminal.

## Test pattern

- Put CLI integration tests in each app's `tests/cli.rs`. Use
  `assert_cmd::cargo::cargo_bin_cmd!`, `predicates`, and small local helpers.
  Specify the binary for `bool`: `cargo_bin_cmd!("true")` or `cargo_bin_cmd!("false")`.
- Check exit status, stdout, stderr, and filesystem effects as applicable.
  For byte-oriented behavior compare bytes, not lossy UTF-8 conversions.
- Use `tests/inputs/` and `tests/expected/` for reusable fixtures; inline small cases.
  Cover supported flags, errors, empty input, stdin, and relevant text boundaries.
- Use `assert_fs` or existing `tempfile` helpers for writable files. Set permissions
  on temporary fixtures, not the checkout. Control child-process cwd/environment;
  avoid changing process-global state. Use fixed dates and timestamps.
- `mk-outs.nu` scripts generate expected output with reference commands. Review
  outputs and GNU/BSD differences; record the reference/version when adding fixtures.
  Normal tests must not require Nushell or installed GNU commands.
- Examples: [catr](coreutils/catr/tests/cli.rs) for output/stdin assertions,
  [pwdr](coreutils/pwdr/tests/cli.rs) for environment isolation, and
  [lsr](coreutils/lsr/tests/cli.rs) for temporary permission fixtures.

## Documentation

- Keep the root [README](README.md) as the app index; link each app README back to it.
- App READMEs: one-sentence purpose, implemented capabilities, one or two runnable
  examples, reference manual, and material limitations. State the working directory.
- Use factual, concise prose. No buzzwords, unsupported compatibility claims, or
  full copies of `--help`. Update docs when behavior changes; retain tutorial credits.
- Link GNU Coreutils where applicable; use GNU Grep, GNU Findutils, and util-linux
  references for `grep`, `find`, and `cal`. State when no direct counterpart exists.
- Follow [AGENTS.md](https://agents.md/#examples) for concrete agent instructions,
  [CLI Guidelines](https://clig.dev/#documentation) for examples and clear limits,
  and [ripgrep's README](https://github.com/BurntSushi/ripgrep/blob/master/README.md)
  for purpose/navigation/build-test structure, not its length or promotional copy.

## Benchmarks

- Each CLI should be benchmarked against its original command using equivalent
  inputs/options. Follow [tailr's Nushell/Hyperfine pattern](coreutils/tailr/benches/tail.bench.nu):
  release binary, shared input, representative options, equivalent output handling.
- Verify correctness before timing. Record the reference implementation/version,
  platform, and commands; check paths against the current workspace layout.
- If no direct original exists, document that and choose a meaningful baseline.
  Do not invent comparisons for custom apps or the interactive editor.
- Benchmark implementation for the other CLIs is deferred; do not add or run
  benchmarks as part of a tests-and-documentation-only task.
