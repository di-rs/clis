# cli-tracing

Shared logging, tracing, and error reporting for synchronous CLI applications.
Call `run::<Cli>` once after argument parsing; the diagnostic command name comes
from Clap metadata. Reusable domain operations should return errors to this adapter
and remain callable without initializing diagnostics.

## Integration

Add `cli-tracing` as a path dependency. The examples also use `anyhow`, `clap`,
`log`, and `tracing`; inherit their workspace dependencies. Outside this workspace,
enable Clap's `derive` feature and tracing's `attributes` feature when using
`#[tracing::instrument]`.

```rust,no_run
use clap::Parser;
use std::process::ExitCode;

#[derive(Parser)]
#[command(name = "example")]
struct Cli {
    #[command(flatten)]
    logging: cli_tracing::LogArgs,
}

fn main() -> ExitCode {
    let cli = Cli::parse();
    cli_tracing::run::<Cli>(&cli.logging, || {
        let _stage = tracing::debug_span!("work").entered();
        log::info!("processing three items");
        Ok(ExitCode::SUCCESS)
    })
}
```

For parsed operation arguments, pass a closure such as `|| execute(&cli)`.
The [complete example](examples/clap.rs) includes stdout writes and error context.
Run it from the workspace root:

```sh
cargo run --locked -p cli-tracing --example clap -- --log-level=debug
CLIS_LOG_LEVEL=trace cargo run --locked -p cli-tracing --example clap -- --log-level=off
```

For subcommands, flatten `GlobalLogArgs`, then resolve the effective value after
parsing. It accepts the flag before or after the subcommand and validates only the
selected value, so an explicit flag can override an invalid environment value.
Map resolution errors to Clap's `ValueValidation` before calling `run`:

```rust
# use clap::{CommandFactory, Parser};
# #[derive(Parser)]
# struct Cli {
#     #[command(flatten)]
#     logging: cli_tracing::GlobalLogArgs,
# }
let cli = Cli::try_parse_from(["example", "--log-level", "off"])?;
let logging = cli.logging.resolve().map_err(|error| {
    Cli::command().error(clap::error::ErrorKind::ValueValidation, error)
})?;
// Pass &logging to cli_tracing::run::<Cli>.
# Ok::<(), anyhow::Error>(())
```

## Configuration and output

| Setting | Behavior |
| --- | --- |
| `LogArgs` | Adds `--log-level`; no short alias. |
| `GlobalLogArgs` | Adds global `-L/--log-level` for subcommand CLIs. |
| Levels | `off`, `error`, `warn`, `info`, `debug`, `trace`. |
| Precedence | Explicit flag, then `CLIS_LOG_LEVEL`, then `off`. |
| Log output | Timestamped text on stderr, with ANSI disabled. |
| Operation errors | Plain `command: context: cause` on stderr, including at level `off`. |

`RUST_LOG` is not read. The adapter leaves stdout to the operation and reports the
error cause chain without printing a backtrace. Clap handles help, version, and
argument errors before diagnostics are initialized.

## Execution and errors

`run` installs a global tracing subscriber and a `log` bridge before invoking the
operation, including at level `off`. A second initialization fails before its
operation runs. Do not install another logger or subscriber alongside this adapter.

| Outcome | Returned status |
| --- | --- |
| Operation returns `Ok(status)` | Preserves that status, including nonzero values. |
| Initialization or operation fails | Exit status 1. |
| Diagnostic write or flush fails | Exit status 1. |

The operation must flush its own data buffers and join workers before returning.
The adapter closes its command span, reports operation errors, then checks
diagnostic writes and flushing. Output may already be partial when a failure
occurs; broken stderr can prevent error messages from being delivered.

This adapter uses synchronous stderr and returns `ExitCode`. Commands requiring
exact reference diagnostics, other failure codes, or terminal-specific reporting
need their own CLI integration.

## Instrumentation

Use `log` for messages and `tracing` spans for operation boundaries. Add
`#[tracing::instrument(level = "debug", skip_all)]` to record a stage without
dumping its arguments. Record explicit counts when useful; retain a span handle
when updating fields because `Span::current()` may select the caller's span if
the stage is filtered out.

At `debug` and `trace`, span-close events report busy and idle elapsed time.
The adapter adds an outer `command` span. Debug spans and their field evaluation
are disabled at lower levels. Nested durations overlap and must not be summed.
Worker events use the global subscriber; propagate parent span context explicitly
when needed.

See the [public API](src/lib.rs), [process tests](tests/cli.rs), and
[workspace testing guide](../../docs/testing.md) for implementation and validation.
