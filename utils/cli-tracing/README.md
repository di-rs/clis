# cli-tracing

One application setup for logging, tracing, and errors. Flatten `LogArgs` into
Clap and call `run::<Cli>` once. The command name comes from Clap metadata,
including `#[command(name = "...")]`; no repeated name string is needed.

## Integration

Within this workspace, add `cli-tracing` by path and inherit `anyhow`, `clap`,
`log`, and `tracing`. Enable Clap's `derive` and tracing's `attributes` features
when declaring these dependencies outside the workspace.

```rust,no_run
use anyhow::{Context, Result};
use clap::Parser;
use std::{io::{self, Write}, process::ExitCode};

#[derive(Parser)]
#[command(name = "example")]
struct Cli {
    #[command(flatten)]
    logging: cli_tracing::LogArgs,
    // Utility-specific arguments belong here.
}

fn main() -> ExitCode {
    let cli = Cli::parse();
    cli_tracing::run::<Cli>(&cli.logging, execute)
}

#[tracing::instrument(level = "debug", skip_all)]
fn execute() -> Result<ExitCode> {
    log::info!("processing three items");
    let mut stdout = io::stdout().lock();
    writeln!(stdout, "3").context("Cannot write result")?;
    stdout.flush().context("Cannot flush result")?;
    Ok(ExitCode::SUCCESS)
}
```

For an operation needing parsed arguments, pass a closure:
`cli_tracing::run::<Cli>(&cli.logging, || execute(&cli))`.
[Biggie](../../biggie/src/main.rs) is the first consumer. The complete
[example](examples/clap.rs) can be run from the workspace root:

```sh
cargo run --locked -p cli-tracing --example clap -- --log-level=info
cargo run --locked -p cli-tracing --example clap -- --log-level=debug
CLIS_LOG_LEVEL=trace cargo run --locked -p cli-tracing --example clap -- --log-level=off
```

## Configuration and output

There is one shared flag: `--log-level=off|error|warn|info|debug|trace`.
Clap resolves explicit flag > `CLIS_LOG_LEVEL` > `off`. Invalid effective values
are parsing errors; an explicit flag overrides even an invalid environment value.
There are no shared short aliases, file/JSON settings, or separate timing switch.
`RUST_LOG` and other `CLIS_*` variables are not read by this setup.

Logs use tracing-subscriber's built-in text formatter, timestamps, and stderr,
with ANSI disabled. The helper never writes stdout. Redirect stderr through the
shell when needed. Required error messages remain plain `command: context: cause`
at every log level, appear once, and are not timestamped optional log events.
Backtrace capture remains governed by `RUST_BACKTRACE`/`RUST_LIB_BACKTRACE`;
the concise reporter prints the cause chain, not a backtrace.

The helper installs a global subscriber and the `log` bridge before calling the
operation, even at level off. Calls such as `log::info!` work automatically;
dependencies using `log` feed the same collector. Native tracing events work too.
Do not install another logger/subscriber or a reverse tracing-to-log bridge.
A second initialization fails before the operation; this helper is for process
entry, not repeated library calls. Clap handles help, version, and parsing errors
before `run`; those paths do not install diagnostics.

## Instrumentation

Use `log` for messages and `tracing` spans for operation boundaries. A log message
alone has no duration. `#[tracing::instrument(level = "debug", skip_all)]` adds a
stage span without dumping function arguments. Add explicit bounded count fields
when useful; record completed counts only after success. When updating fields,
keep an explicit span handle: `Span::current()` may refer to the caller
when your stage is filtered out.

At debug/trace verbosity the built-in span-close records include busy and idle
times. The helper adds a `command` span around the operation. Debug spans are
disabled at lower verbosity, including their field evaluation. Span timings are
elapsed time, not CPU time; nested/overlapping durations must not be summed.
They locate work to investigate, not prove a speedup.

Worker threads see the global subscriber, but parent span context still needs
explicit propagation when desired. Join workers and drop stage spans before the
operation returns. This synchronous entry helper does not drive an async runtime;
async consumers need a separate, deliberate integration.

Domain libraries may emit `log` events and tracing spans but must not depend on
this adapter, parse arguments, or initialize global state. Return typed errors;
convert them to `anyhow` with context at the CLI boundary. Library calls remain
usable without any collector, or with a host-provided subscriber.

## Errors and lifecycle

`run` returns `Ok(ExitCode)` from the operation unchanged, including expected
nonzero outcomes. An unhandled `anyhow` error, initialization error, or reported
diagnostic write/flush failure returns 1. The operation must flush its own data
buffers. The helper closes its command span, reports any operation error once,
and checks diagnostic writes/flush before returning. Partial data may already exist.
It neither calls `process::exit` nor installs panic hooks.

The small private checked writer retains errors that formatter callbacks cannot
return to the caller. It uses synchronous `std::io::stderr`, including Rust's
handling of invalid standard-stream descriptors. There is no background queue or
public sink/subscriber configuration API. If both operation and diagnostics fail,
both reports are attempted; a broken stderr may prevent their delivery.

Ports with other failure codes, per-operand reporting, or exact GNU diagnostic
formatting need an adapter reviewed against that contract before adoption. The
current helper is not a universal exit-policy implementation. Full-screen apps
also need a terminal-safe diagnostic policy before migration.

## Verification and overhead

Process tests exercise actual installation, the log bridge, level filtering,
worker events, Clap naming, error causes, nonzero statuses, repeated installation,
and broken stderr. Private writer tests cover retained write/flush errors.
Biggie adds precedence, validation-before-mutation, stage, and direct-library tests.

The [benchmark](benches/overhead.rs) checks a deterministic checksum on every
sample. Build and run separately for each level; capture CSV from stdout and
redirect verbose stderr away from the terminal:

```sh
cargo bench --locked -p cli-tracing --bench overhead -- --bare --log-level=off
cargo bench --locked -p cli-tracing --bench overhead -- --log-level=off
cargo bench --locked -p cli-tracing --bench overhead -- --log-level=debug 2>/dev/null
```

These measure repeated stage instrumentation; initialization is outside the
sample. [Recorded evidence](overhead.md) also distinguishes startup measurements
from stage overhead and lists what remains unmeasured.

[Shared policy](../../docs/observability.md) ·
[Upstream initialization and log bridge](https://docs.rs/tracing-subscriber/latest/tracing_subscriber/util/trait.SubscriberInitExt.html) ·
[Clap reusable arguments](https://docs.rs/clap/latest/clap/_derive/) ·
[Span timing](https://docs.rs/tracing-subscriber/latest/tracing_subscriber/fmt/struct.Layer.html#method.with_span_events)
