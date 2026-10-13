# cli-log

Shared configuration and command lifecycle helpers for the workspace's CLIs.
Libraries emit standard [`log`](https://docs.rs/log/latest/log/) events.
[`flexi_logger`](https://docs.rs/flexi_logger/latest/flexi_logger/) handles JSON
formatting, file I/O, rotation, cleanup, filtering, and optional stderr output.
There is no custom logging backend. Tracing spans are unnecessary for inspecting
local command activity and errors.

## Configuration

| Setting | Behavior |
| --- | --- |
| `CLIS_LOG_DIR` | Shared log directory override; relative paths use the working directory. |
| `CLIS_LOG_LEVEL` | File threshold: `off`, `error`, `warn`, `info`, `debug`, `trace`; default `info`. |
| `CLIS_LOG_STDERR` | Independent text logging to stderr; same levels, default `off`. |
| `CLIS_LOG_SESSION` | Optional shell session identifier recorded at startup. |

Default directory: absolute `$XDG_STATE_HOME/clis/logs` on Unix when configured;
otherwise `$HOME/Library/Logs/clis` on macOS, `$HOME/.local/state/clis/logs` on
other Unix, or `$LOCALAPPDATA/clis/logs` on Windows. Relative `XDG_STATE_HOME` is
ignored. `RUST_LOG` is not used. Invalid environment levels warn and use defaults;
unavailable storage warns without failing the command.

Biggie's `-L/--log-level` overrides the file threshold. Grepr's verbosity flags
select its console threshold. Kara disables console logging to protect its UI.
Logging never writes to stdout. Required terminal diagnostics still print when
logging is off, and already-reported errors are persisted without printing again.

```sh
CLIS_LOG_DIR=/tmp/my-cli-logs CLIS_LOG_LEVEL=debug biggie bytes - -b 16
CLIS_LOG_LEVEL=off catr input.txt
```

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
See the [complete example](examples/clap.rs).

## Files and the future dashboard

Each invocation gets its own files, avoiding concurrent processes rotating the
same file. An active filename is
`<command>_<pid>-<startup-nanoseconds>_rCURRENT.jsonl`; rotated files use numbered
suffixes. Use command and invocation from the filename to group all its records.
The backend rotates at 5 MiB and retains three rotated files **per invocation**.
Retention across old invocations belongs to the future shell's log management;
this utility does not impose a directory-wide size or age limit.

Use `flexi_logger`'s native JSON Lines format: `timestamp`, uppercase `level`,
`text`, optional source/thread details, and structured values under `kv`.
For example, a completion record contains:

```json
{"level":"INFO","timestamp":"2026-10-12T14:00:00Z","text":"command finished","kv":{"event":"command_finished","exit_code":0,"elapsed_ms":12}}
```

The `command_started` event's `kv` includes `command`, `pid`, `invocation_id`,
and optional `session_id`. Other boundary events are `command_finished`, `error`,
`argument_error`, and `panic`. Ordinary library events have no required event
field. Completion records contain numeric `exit_code` and `elapsed_ms`; an
intentional status such as `false` returning 1 is not an error. Lifecycle and
explicit boundary errors bypass the ordinary severity threshold, except `off`,
which disables all persistence. Panic reporting chains the previous hook.

The shell can pass a directory and session identifier, discover invocation files,
and tail complete newline-terminated records. Read numbered files for earlier
records after rotation. A kill or uncaught panic can leave a start without a
finish; reconcile this with child process status. No dashboard, collector, custom
schema, or database is included here.

Writes use the backend's direct mode, without an asynchronous queue. New Unix log
directories are private (`0700`); existing directory permissions stay unchanged.
File permissions follow the process umask. Logging is not an fsync-backed audit trail.
No argv, environment dump, stdin, or file contents are automatically collected.
Application and parser errors can contain paths or rejected values; messages are
not automatically redacted. Grepr logs match activity without matched contents.

## Validation

`cargo nextest run --locked -p cli-log` covers native JSON and fields, thresholds,
configuration, output/status preservation, panic hooks, unavailable storage, and
concurrent invocations. See the [workspace testing guide](../../docs/testing.md)
for broader checks.
