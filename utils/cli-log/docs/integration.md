# Integrate CLI logging

## Integrate a binary

Use `run` to initialize logging, parse arguments, and record the returned exit
status. Install panic/reporting hooks first. Reusable libraries must not initialize
a logger. Flush application data writers and join workers before returning.

```rust,no_run
use clap::Parser;
use std::process::ExitCode;

#[derive(Parser)]
struct Cli {
    input: String,
}

fn main() -> ExitCode {
    cli_log::run("example", |cli: Cli| match std::fs::metadata(&cli.input) {
        Ok(metadata) => {
            log::info!(bytes = metadata.len(); "input inspected");
            0
        }
        Err(error) => {
            cli_log::diagnostic!("example: {error}");
            1
        }
    })
}
```

For a command returning a rich error report, `try_run("name", |cli| run(&cli))`
logs failures, records status 1, and returns the original error for the existing
reporter. Success records status 0. Both helpers handle Clap exits without running
the operation or recording completion twice. Specialized parsing and console
setup can still use `init`, `Session::parse`, and `Session::finish` directly.

`record_error` persists an error already displayed in a rich report or message
bar. `diagnostic!` also prints a plain stderr line. `Session::parse` preserves
Clap output/status and records argument errors; custom parsers can use
`Session::clap_error`. `LogArgs` and `GlobalLogArgs` provide opt-in Clap flags for
use with `init_with_level`. Biggie does not persist help/version invocations.
See the [complete example](../examples/clap.rs).
