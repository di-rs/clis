# Errors, logging, and tracing

[North star U9](north-star.md#u9--errors-and-observability) ·
[Architecture](architecture.md) · [Setup and API](../utils/cli-tracing/README.md)

Use one shared application setup: Clap `LogArgs` plus `cli_tracing::run::<Cli>`.
The helper gets the command name from Clap and installs logging and tracing before
running the operation. `biggie` is the first consumer; other utilities require
individual migrations and evidence. A standard is not proof of adoption.

## Errors belong to the caller

Domain APIs return `io::Error` when sufficient, otherwise a typed `thiserror`
error with sources and relevant context. CLI orchestration uses `anyhow::Result`
and `.context(...)`. Do not add a catch-all shared domain error enum or make
`anyhow::Error` the default public domain error.

Required diagnostics are independent of optional logs. Report a failure once,
with concise context and causes, even at log level off. Returning an error to
the shared runner replaces separate `eprintln!`/`log::error!` calls for that same
failure. Recoverable conditions and useful progress remain ordinary log events.
Do not log an error at every `?` propagation boundary.

Keep each utility's diagnostic/status contract. The initial runner preserves
explicit successful outcomes (including expected nonzero statuses) and maps
unhandled errors to exit 1. Ports needing other error codes, multiple-operand
reporting or exact GNU diagnostic wording must adapt those semantics before
migration; do not silently normalize them to this helper's default.

Backtraces are separate from verbosity. Respect `RUST_BACKTRACE` and
`RUST_LIB_BACKTRACE`; do not change process environment to enable them. The
shared concise reporter prints causes without a backtrace or panic-style report.

## One configuration contract

Expose `--log-level=off|error|warn|info|debug|trace` through the shared Clap group.
Precedence is explicit flag > `CLIS_LOG_LEVEL` > off. Reject invalid effective
values before the domain operation. Preserve GNU meanings of `-v`, `-q`,
`--verbose`, and `--quiet`; there are no shared short verbosity aliases.

Use built-in text formatting on stderr, without ANSI. Keep stdout for the
utility's data/status contract. There are no file/JSON options or separate timing
switch; debug/trace verbosity enables stage-close timing records. Users can
redirect stderr. Extra configuration belongs in a future demonstrated requirement,
not a menu of per-utility subscriber setups.

Clap handles help, version, and parsing errors before diagnostics installation.
Commands whose reference grammar cannot accept extra flags need a reviewed
integration before migration. Full-screen apps, including Kara, must not corrupt
the display with stderr logs; their diagnostic routing remains separate work.

## Instrumentation and lifecycle

Use `log` for messages and `tracing` for spans. The installed bridge attaches log
records to the active tracing span. It cannot infer operation boundaries or turn
formatted message values into structured span fields. Domain libraries use these
facades without initializing a collector or depending on `cli-tracing`.

Severity should describe useful information: error for a failed action when the
caller is not also reporting it, warn for a recoverable exceptional condition,
info for progress/completion, debug for decisions/counts, and trace for detailed
control flow. Do not manufacture messages to exercise every level or emit per-record
logs in hot loops. Do not log credentials, input contents or full argv by default.

Instrument bounded stages at debug, for example with
`#[tracing::instrument(level = "debug", skip_all)]`. Add explicit count fields;
record completed work only after success and label intended counts as requested.
Update fields through the stage handle, not an implicitly current parent span.
The shared runner adds a command span. At debug/trace, use the formatter's built-in
close records with timestamps and busy/idle durations; no custom timing schema.
Busy time includes waiting while a span is entered; idle time covers the rest of
its lifetime. Neither is CPU time, and nested/overlapping durations are not additive.

Initialize once at the CLI boundary. The global subscriber also collects worker
logs; propagate parent span context explicitly when needed. Join workers and drop
spans before returning. Do not hold entered-span guards across `.await`; the
current synchronous helper is not an async runtime. Library calls must work
without initialization and with their host's subscriber.

Flush owned data buffers before returning. The runner closes the command span,
reports an operation error, and checks diagnostic writes/flush. Diagnostic errors
reported by the underlying writer make the command fail; partial data may exist.
Both operation and sink errors must remain visible when stderr permits delivery.
Use normal returns so destructors run; do not bypass cleanup with `process::exit`.

## Evidence and migration

Test effective configuration/precedence, every severity, the log bridge, spans at
debug/trace, disabled field evaluation, library calls without setup, command naming,
expected statuses, duplicate initialization, diagnostic write/flush failures, and
unchanged ordinary stdout/stderr. Test workers when introducing concurrent consumers.
Compare correctness-checked instrumentation and startup costs; timing logs identify
candidates, while [benchmarks](benchmarking.md) establish performance claims.

Migrate remaining direct `color-eyre` consumers (`parsu`, `mkdirr`, `touchr`, `pwdr`,
Kara) individually. Review `grepr`'s separate logger and input-content warnings with
its GNU flag/error policy. These are follow-ups, not evidence provided by this crate.

## Design sources

[Anyhow](https://docs.rs/anyhow/latest/anyhow/) supplies application error context;
[thiserror](https://docs.rs/thiserror/latest/thiserror/) supplies typed library errors.
[Clap](https://docs.rs/clap/latest/clap/_derive/) supports flattened shared arguments.
[Tracing initialization](https://docs.rs/tracing-subscriber/latest/tracing_subscriber/util/trait.SubscriberInitExt.html)
can install the log bridge, and [tracing-log](https://docs.rs/tracing-log/latest/tracing_log/)
describes event conversion. The [formatter](https://docs.rs/tracing-subscriber/latest/tracing_subscriber/fmt/)
owns text output and stage timings.
