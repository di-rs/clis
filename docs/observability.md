# Errors, diagnostics, and stage timings

[North star U9](north-star.md#u9--errors-and-observability) ·
[Architecture](architecture.md) · [Shared implementation](../utils/cli-support/README.md)

This is the shared contract. Adoption remains per utility: `biggie` is the first
consumer; other commands, including Kara, need separate migrations and evidence.

## Errors belong to the caller

Domain APIs return `io::Error` when sufficient, otherwise a typed `thiserror`
error with sources and relevant context. CLI orchestration uses `anyhow::Result`
and `.context(...)`; it maps failures to the utility's diagnostics and exit codes.
Do not expose `anyhow::Error` as the default domain error or add a catch-all shared
error enum. Preserve GNU diagnostic/status semantics in ports.

Required error messages are independent of optional logging. Print each failure
once; a lower log level must not suppress it. Use concise context and causes by
default. Backtraces are a separate diagnostic choice (`RUST_BACKTRACE` and
`RUST_LIB_BACKTRACE`); never change process environment to enable them or equate
verbosity with backtrace policy. CLI adapters may offer a separate developer
report, but normal diagnostics must not contain a panic-style report.

## Configuration and output

Use explicit `--log-level=off|error|warn|info|debug|trace`, `--log-format=text|json`,
`--log-file=PATH`, and `--timings=true|false` where the command's grammar permits.
`--log-file=-` selects stderr. Environment counterparts are `CLIS_LOG_LEVEL`,
`CLIS_LOG_FORMAT`, `CLIS_LOG_FILE`, and `CLIS_TIMINGS`. Resolve each field as
explicit CLI value, then environment, then default. Defaults are off, text, stderr,
and false. Reject invalid effective values before performing the domain operation;
an overridden invalid environment value is irrelevant. Environment-only integration
is appropriate for commands that cannot accept extra flags.

Preserve GNU meanings of `-v`, `-q`, `--verbose`, and `--quiet`. Existing aliases
on custom apps may remain, with documented precedence. No workspace-wide short
verbosity flags are imposed. Full-screen apps must route diagnostics to an explicit
file or their UI; stderr logging must not corrupt the display.

Logging and timing use stderr by default, never the data stream. File destinations
create a new file and fail if it already exists, including symlinks; they never
truncate an existing file. Reject a data destination that aliases the diagnostic
file before opening it for truncation. Files are synchronous: no unbounded queue,
background worker, automatic rotation, or promise of crash durability. The caller
owns retention and filesystem permissions. Do not log input contents, credentials,
or full argv by default. Plain text has no automatic ANSI styling; JSON is one
object per line. JSON diagnostics do not change the command's data format.

## Instrumentation and lifecycle

Domain crates depend only on the `tracing` facade. Emit useful operation events:
error for an actual failed action when the caller is not also reporting it, warn
for a recoverable exceptional condition, info for completion/progress milestones,
debug for decisions/counts, and trace for detailed control flow. Do not manufacture
messages just to exercise every level or log every record in a hot loop.

Use spans with target `clis::timing` for bounded operation/stage summaries. Record
counts such as lines or bytes as structured fields. Fields describe completed work
only when updated after success; label intended counts as requested. With timing
collection disabled these spans have no timer or retained fields under the shared
subscriber. With it enabled, closing a span emits its name, fields, and elapsed
wall-clock milliseconds in the selected format, regardless of the log level.
Elapsed time includes waiting and the span's whole lifetime; it is neither CPU time
nor a sum that can safely combine nested/overlapping spans. Drop stage spans before
finishing the subscriber. Summary records are per stage invocation, not an unbounded
history or a timeline export.

`cli-support` builds an explicit subscriber and provides a scoped dispatch; it
never installs a process-global subscriber, panic hook, or logger. A host can
compose its own subscriber instead. Scoped dispatch applies to the current thread;
pass a cloned dispatch explicitly to worker threads. For async code use the
tracing future instrumentation/dispatch facilities rather than holding a span
entry guard across `.await`. Domains must work without any subscriber.

The CLI boundary opens the sink, enters the dispatch, runs the operation, closes
spans, calls `finish()` to check write/flush errors, and returns an exit status.
Do not bypass destructors with `process::exit` while owning buffered resources.
If operation and sink both fail, retain both diagnostics. Sink failure is a command
failure, even when domain output succeeded; partial output may already exist.

## Evidence and migration

Test configuration precedence and invalid values, all severity thresholds, text and
JSON records, timing with logging off, library calls without setup, repeated scoped
use, file collisions, write/flush failures, and unchanged ordinary stdout/stderr.
Test actual worker propagation when introducing concurrent consumers. Benchmark
instrumentation off and on with identical correctness-checked work; logging output
and terminal costs need their own workload evidence. Timings locate candidates;
[benchmark comparisons](benchmarking.md) establish performance claims.

Use `cli-support` for CLI-side diagnostic collection and stage timing.
Migrate remaining direct `color-eyre` consumers (`parsu`, `mkdirr`, `touchr`, `pwdr`,
Kara) individually, preserving domain error contracts. Migrate `grepr`'s separate
logger and input-content warnings with its GNU flag/error policy; do not turn its
`-v` into verbosity. These migrations are follow-ups, not evidence supplied by the
shared crate's tests.

## Design sources

[Anyhow](https://docs.rs/anyhow/latest/anyhow/) covers contextual application errors;
[thiserror](https://docs.rs/thiserror/latest/thiserror/) covers typed library errors.
[Tracing](https://docs.rs/tracing/latest/tracing/) separates instrumentation from
collection. [Tracing subscriber formatting](https://docs.rs/tracing-subscriber/latest/tracing_subscriber/fmt/)
provides the event formats; the shared adapter selects its sink explicitly.
[Rust process exit](https://doc.rust-lang.org/std/process/fn.exit.html) explains why
owned resources must finish before termination.
