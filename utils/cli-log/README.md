# cli-log

`cli-log` gives the Rust CLIs consistent log configuration and records when a
command starts, finishes, or fails. It uses the `log` facade and `flexi_logger`
for file output and rotation. Logs never go to stdout, so pipelines keep their
normal command output.

## Configure a command

| Variable | Meaning | Default |
| --- | --- | --- |
| `SANCTUARY_LOG_DIR` | Directory for invocation log files | Platform state/log directory |
| `SANCTUARY_LOG_LEVEL` | File threshold | `info` |
| `SANCTUARY_LOG_STDERR` | Independent stderr threshold | `off` |
| `SANCTUARY_LOG_SESSION` | Optional shell session identifier | Unset |

Levels are `off`, `error`, `warn`, `info`, `debug`, and `trace`. For example,
from the workspace root:

```sh
SANCTUARY_LOG_LEVEL=off cargo run -p catr -- Cargo.toml
```

On Unix, an absolute `XDG_STATE_HOME` selects `sanctuary/logs` beneath it.
Otherwise, macOS uses `~/Library/Logs/sanctuary`, other Unix systems use
`~/.local/state/sanctuary/logs`, and Windows uses `$LOCALAPPDATA/sanctuary/logs`.
Relative directory overrides use the command's working directory. `RUST_LOG`
is ignored; invalid levels and unavailable storage produce warnings.

Biggie's `-L` overrides its file threshold, Grepr's verbosity controls console
logging, and Kara disables console logging to protect its interface.
Required command diagnostics still appear when logging is off.

## Add logging or read its output

Binary authors should follow [integration](docs/integration.md). Libraries emit
ordinary `log` events and leave logger initialization to the binary.
For file names, JSON fields, lifecycle events, and rotation, see
[log-file format](docs/log-files.md).

Each invocation retains its own rotated files; there is no directory-wide
retention limit. Logs are not an audit trail. Arguments and file contents are not
automatically collected, but error messages can contain paths or rejected input
and are not automatically redacted.

Run package tests with `cargo nextest run -p cli-log`.
