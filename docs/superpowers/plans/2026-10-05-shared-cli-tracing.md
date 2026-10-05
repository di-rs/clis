# Shared CLI tracing implementation plan

> **For agentic workers:** Use the executing-plans skill to implement this plan
> task by task. Execution is authorized in the user request; proceed after self-review.

**Goal:** Provide shared, tested diagnostics and opt-in
stage timing support, using biggie as the first real consumer.

**Architecture:** Domain libraries emit tracing events/spans and retain typed errors.
A separate CLI tracing crate resolves typed configuration, owns a checked sink and
scoped subscriber, and formats events/stage summaries. Biggie's adapter uses anyhow
context and explicit exit handling; its domain API remains an io::Result operation.

**Tech Stack:** Rust workspace nightly, anyhow, tracing, tracing-subscriber,
serde_json, thiserror, Clap in the app only, nextest and Clippy.

**Spec:** [Errors, diagnostics, and stage timings](../../observability.md).

## Global constraints

- CLI > environment > default, field by field; off/text/stderr/false defaults.
- Keep domain code free of argv, process-global initialization, streams and exits.
- Preserve GNU verbosity meanings. Keep biggie's existing aliases with explicit
  log-level precedence; do not alter unrelated apps or Kara.
- File sinks create new files; stdout stays the data/status stream.
- Sink errors are observable; timings are optional wall time, not a speed claim.
- No handoff files or scratch staging directories enter commits.

## Review focus

1. A log file that aliases data must never be truncated (task 3 regression).
2. A sink that fails after successful writes must make finish fail (task 2 test).
3. Logging off must still allow timings, but do no timing work otherwise (task 2).
4. Concurrent callers must use explicit dispatch and serialize records (task 2).
5. Failure after partial data output must not report success (task 3 injected I/O).

## Task 1: Typed configuration

**Files:** root Cargo.toml/Cargo.lock; utils/cli-tracing/Cargo.toml,
src/lib.rs, src/config.rs, tests/config.rs.

**Interfaces:** Config holds LevelFilter, Format, Destination, timings. Overrides
holds optional versions of each. Config::resolve takes Overrides and an injected
OsString environment lookup and returns Result<Config, ConfigError>. No Clap types.

- [x] Add tests for defaults, per-field precedence, overridden bad environment,
  invalid effective values and non-UTF-8 destination paths.
- [x] Run `cargo test -p cli-tracing --test config`; expect missing API failure.
- [x] Implement config parsing with typed errors; retain native paths.
- [x] Run the same command; expect all config tests pass.

## Task 2: Subscriber, sinks and timings

**Files:** utils/cli-tracing/src/{lib,session,sink,timing}.rs,
tests/session.rs, benches/overhead.rs, README.md.

**Interfaces:** TracingSession::new(&Config) opens destination; TracingSession::with_writer(&Config,
impl Write + Send + 'static) injects an owned sink. dispatch() returns &Dispatch;
finish() returns io::Result<()> and checks prior writes plus flush. TracingSession never
installs global state. Stage spans have target clis::timing; close emits a timing
record independently of normal event level. Built-in tracing formatter owns events.

- [x] Add failing tests for formats, levels, timing, sink failure, scoped repeated
  use, threaded dispatch and safe file opening; run `cargo test -p cli-tracing`.
  Expected: session API missing before implementation.
- [x] Implement a synchronized checked writer, filtered fmt layer, and a layer
  retaining only currently open stage timestamps/fields.
- [x] Run package tests and Clippy; expected: all pass without broad suppressions.
- [x] Add a standalone deterministic checksum benchmark for bare/disabled/enabled
  instrumentation, assert equivalent checksums, report raw repeated samples.
- [x] Run `cargo bench -p cli-tracing --bench overhead`; expected: equal checksums
  and raw timings. Record platform/limits; make no reference CLI speed claim.

## Task 3: Consumer integration

**Files:** biggie/Cargo.toml, src/{cli,main,lib}.rs, tests/{cli,library}.rs, README.md;
root README and Cargo.lock.

**Interfaces:** biggie keeps gen_random_lines(impl Write, u64) -> io::Result<()>;
uses tracing only in domain code. CLI constructs Overrides and calls task 1/2 APIs.

- [x] Add CLI regressions for explicit/environment diagnostics, timing with logging
  off, invalid settings before output creation, file aliases, concise errors and
  unchanged default behavior; add domain write failure/repeated call tests.
- [x] Run `cargo nextest run -p biggie`; expect new options/tests to fail first.
- [x] Use shared diagnostic collection and anyhow at the CLI edge, preserve
  aliases, explicitly flush data, close spans and check diagnostics before success.
- [x] Run package tests/doctests/Clippy; expected: all pass.

## Task 4: Standards, evidence and delivery

**Files:** docs/observability.md, north-star.md (U9), architecture.md, benchmarking.md,
utility template, AGENTS.md, PR template, explore/audit skills, relevant READMEs.

- [x] Establish docs before code and reconcile with the final implementation.
- [x] Validate local links, examples, skills and final diff. Run CONTRIBUTING's
  workspace fmt/Clippy/nextest/doctest checks, retaining actual commands/counts.
- [x] Obtain one independent final review; fix consequential findings with
  regressions. Record limitations and any implementation rulings.
- Delivery: commit focused task paths, update the existing authorized PR against the
  current default branch, and verify CI. Do not merge.

The clean starting revision is 0a10b46. All six baseline biggie tests passed.

## Execution evidence

Implemented with biggie as the first consumer. Local macOS verification passed:
729 workspace tests, 4 doctests, fmt and strict workspace Clippy. The shared
[overhead record](../../../utils/cli-tracing/overhead.md) retains checked raw samples.
Independent review led to regressions for partial writes without success
instrumentation, retained first sink failures, and final-close timing records.
Clap and log-bridge examples validate application-owned setup.
Hosted Linux validation and delivery status are recorded in the PR checks.
