# Repository guidance

## Mission and priorities

Evolve the existing Rust utilities into compatible, fast, reusable building blocks
for both command-line users and future Rust applications. These are targets, not
claims that the current implementations already meet them.

1. Close GNU and BSD option **and behavior** gaps in existing utilities first.
2. Match or exceed reference performance on equivalent, reproducible workloads.
3. Keep a consistent, readable, maintainable structure with reusable library APIs.

Correctness, safety, and library boundaries are constraints throughout, not work
postponed until after optimization. A faster incompatible result is not a win.
Do not trade away existing behavior or maintainability for an unmeasured speedup.

## Read before changing code

- [README](README.md): app index, current status, quick start.
- [CONTRIBUTING](CONTRIBUTING.md): checks and review workflow.
- [Architecture](docs/architecture.md): read when changing APIs, structure, or reuse.
- [Compatibility](docs/compatibility.md): read when changing flags or behavior.
- [Benchmarking](docs/benchmarking.md): read when changing hot paths or claiming speed.
- Read the affected app README, manifest, implementation, tests, and any nearer
  `AGENTS.md`. Keep this root file focused; put utility-specific exceptions nearby.

## Scope and implementation workflow

- Prefer one utility or one reusable capability per change. State the reference
  behavior, affected library API, tests, and performance impact before editing.
- Establish the affected-package baseline. Add a failing regression for a bug or
  missing behavior, implement the library change, then connect the CLI adapter.
- Preserve unrelated work and tutorial credits. Do not rewrite the workspace,
  rename every package, or modify Kara for unrelated utility work.
- Shared code and compatibility work are encouraged when relevant. Extract a
  focused helper for real consumers; do not build a speculative universal framework.
- Existing inconsistencies are migration work, not permission to copy a bad pattern.
  Improve the touched boundary incrementally; report larger follow-up work.

## GNU and BSD compatibility

- Target the combined option surface of GNU and explicitly named BSD implementations.
  Record exact reference versions and platforms; "BSD" alone is not a test target.
- Acceptance includes parsing, defaults, option interactions, output bytes, stderr,
  exit status, stdin/TTY behavior, environment, and filesystem effects as relevant.
- Conflicting flag meanings need a documented per-utility resolution. Preserve the
  current default until that decision is made. Do not silently choose by host OS,
  reject valid combinations for parser convenience, or invent a compatibility flag.
- Keep a per-utility compatibility table in its README or a linked document. Mark
  unverified, partial, missing, and intentionally different behavior explicitly.
  A listed flag or a passing old test suite does not prove reference parity.
- Use manuals and executable behavior as references. Keep implementation original;
  do not copy GNU implementation code into this MIT repository. Record attribution
  and review licensing before importing third-party code, tests, or fixtures.

## Library-first structure and code quality

- `src/main.rs`: process setup, standard streams, diagnostic rendering, exit mapping.
- `src/cli.rs`: Clap definitions and conversion into validated domain options.
- `src/lib.rs`: public, CLI-independent API; move growing internals into named modules.
  Small utilities may use fewer files, but still expose their useful operation as a
  library. Consistent responsibilities matter more than identical file counts.
- The CLI calls the same library implementation used by Rust consumers. An API
  taking only argv or spawning the binary does not satisfy library reuse.
- Do not expose Clap types in domain APIs. Accept typed options, `Path`/`PathBuf`,
  and caller-owned readers/writers or explicit resources. Validate invariants for
  direct Rust callers too, not only through Clap.
- Domain code must not parse process arguments, terminate the process, initialize
  global logging, change cwd/environment, or print to global stdout/stderr. Return
  typed results/errors and meaningful outcomes; the adapter owns process policy.
- Handle arbitrary bytes and non-UTF-8 paths where the command contract requires it.
  Do not use lossy conversion or line/string APIs that change byte semantics.
- Prefer streaming, buffer reuse, explicit ownership, and narrow APIs. State memory
  bounds; do not read a whole input merely for convenience. Flush owned output
  buffers and propagate write/flush failures before reporting CLI success.
- Reuse `[workspace.dependencies]` and `[lints] workspace = true` when applicable.
  Use existing error conventions at adapters; prefer matchable errors in libraries.
  No blanket lint suppression, unchecked failure paths, or unjustified `unsafe`.
- Extract shared mechanisms under focused `utils/` crates once real callers need
  them. Keep utility-specific semantics local. Do not force unrelated commands
  into a common runner, global error type, or identical option semantics.
- Document public APIs with examples, error/side-effect behavior, and relevant
  complexity. Test library use directly; preserve or explain API compatibility.

## Tests and checks

Run from the workspace root; replace `catr` with the affected package:

```sh
cargo nextest run --locked -p catr
cargo test --locked -p catr --doc
cargo clippy --locked -p catr --all-targets --all-features
cargo fmt --all -- --check
```

Use the doctest command for packages with a library target. For Rust changes,
follow with the workspace checks in [CONTRIBUTING](CONTRIBUTING.md#checks).
For docs-only changes, check links, commands, examples, and the diff; report any
example that was not compiled. Do not claim checks ran when tools are unavailable.

- Put CLI integration tests in `tests/cli.rs`; use
  `assert_cmd::cargo::cargo_bin_cmd!`, `predicates`, and small helpers. For `bool`,
  select `cargo_bin_cmd!("true")` or `cargo_bin_cmd!("false")` explicitly.
- Add direct library tests, including injected read/write errors where relevant.
  Cover flag combinations, boundaries, stdin, empty and unterminated inputs,
  invalid bytes, invalid options, and multi-file/partial-failure behavior.
- Assert status, stdout, stderr, and side effects, not just successful parsing.
  Compare byte-oriented output as bytes. Do not weaken or ignore tests to hide bugs.
  Change an old expectation only with documented evidence of the intended behavior.
- Use `tests/inputs/` and `tests/expected/` for reusable fixtures. Use `assert_fs` or
  existing `tempfile` helpers for writes; never chmod or mutate checkout fixtures.
  Control child cwd/environment, timestamps, timezone, and locale; avoid global state.
- Keep ordinary tests hermetic. Reference comparisons and `mk-outs.nu` regeneration
  are separate, explicit workflows; record reference versions and review each diff.
- Existing examples: [catr](coreutils/catr/tests/cli.rs) for CLI I/O,
  [pwdr](coreutils/pwdr/tests/cli.rs) for environment isolation, and
  [lsr](coreutils/lsr/tests/cli.rs) for temporary permission fixtures.
  Changes to Kara also need affected interactive behavior checked in a terminal.

## Performance evidence

- Build release binaries before timing. Verify equivalent results first, then
  compare the candidate with the previous Rust revision and each relevant GNU/BSD
  reference on the same host. Record versions, commands, inputs, and environment.
- Cover small/startup and large/throughput cases, streaming and seekable inputs,
  relevant flags, and memory use. Include regressions, variance, and inconclusive
  results; never claim every workload is faster from one favorable measurement.
- Do not use blanket Hyperfine `--ignore-failure`, discard errors, or time less work.
  Expected nonzero statuses require exact validation, not ignoring all failures.
- For behavior/hot-path changes, add or update the affected benchmark and run it
  when the environment permits. Report unavailable baselines as unmeasured.
  Pure documentation changes do not require fabricated timing evidence.

## Documentation and review

- Keep the root README as the app index and project contract. App READMEs link back
  and describe implemented behavior, CLI and library examples, references, gaps,
  and benchmark reproduction where available. Do not paste entire help output.
- Report the scope, compatibility/API impact, commands actually run, outcomes,
  benchmark evidence or reason not measured, and remaining gaps in the PR.
- Follow the [Git conventions](docs/agent-skills.md#git-conventions) for branch
  names, commit messages, and PR titles; use the relevant repository skills.
- Use the [PR template](.github/pull_request_template.md). Do not present proposed
  architecture, planned flags, future CI, or performance targets as implemented.
