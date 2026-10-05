# Biggie

`biggie` generates a text file of random alphanumeric words for testing other programs.
Each line contains 7–14 space-separated words, each 2–11 ASCII characters long.
The destination is created or truncated; existing contents are overwritten.

## Usage and flags

From the workspace root, with a disposable destination:

```sh
cargo run --locked -p biggie -- -n 100 biggie-example.txt
```

| Argument | Behavior |
| --- | --- |
| `FILE` | Destination, default `out.txt`. `-` is an ordinary filename, not stdout. |
| `-n`, `--lines LINES` | Positive line count, default 100,000. |
| `-h`, `--help` | Show help. |
| `-V`, `--version` | Show version. |
| `--log-level LEVEL` | `off`, `error`, `warn`, `info`, `debug`, `trace`; default off. |
| `--log-format FORMAT` | `text` (default) or one JSON object per line. |
| `--log-file PATH` | Create a new diagnostic file; `-` selects stderr (default). Existing paths are rejected. |
| `--timings[=true\|false]` | Collect generation and flush wall times; omitted value means true. Default false. |
| `-v`, `--verbose` | Legacy repeatable aliases: warn, info, debug, trace. |
| `-q`, `--quiet` | Legacy repeatable alias for optional logging off; conflicts with verbose. Does not suppress the completion message. |

Explicit `--log-level` wins over legacy aliases. Either is an explicit override of
`CLIS_LOG_LEVEL`. Format, destination and timings use explicit flag > corresponding
`CLIS_LOG_FORMAT`, `CLIS_LOG_FILE`, `CLIS_TIMINGS` > default. Effective invalid
settings fail before data output. Backtraces are independent; verbosity never changes
the process environment. See the [shared contract](../docs/observability.md).

```sh
cargo run --locked -p biggie -- -n 100 biggie-timed.txt --timings --log-format=json
```

The completion message goes to stdout. Diagnostics and timing summaries go to stderr
or the new log file; data stays in `FILE`. An output path aliasing the new log file
is rejected before truncation. Successful generation emits an info event; debug
records the requested count, trace completion. Timings include `generate` with
requested/completed counts and `flush`. There are no per-line or input-content logs.
Errors always go to stderr. CLI parsing errors exit 2; operation, diagnostic setup,
write or flush errors exit 1. Success exits 0 after explicit data/diagnostic flushing.
An error can leave a partial data file or a newly created diagnostic file.

## Rust library

Use `biggie = { path = "../clis/biggie" }` from a consumer beside this checkout:

```rust
let mut output = Vec::new();
biggie::gen_random_lines(&mut output, 2)?;
# Ok::<(), std::io::Error>(())
```

The [operation](src/lib.rs) accepts a writer and a line count, returning `io::Result<()>`.
Zero lines is valid for direct calls. It does not flush the writer, parse arguments,
configure tracing or use global streams. Write errors preserve already written bytes.
The CLI and library share this operation; callers may supply their own subscriber.

## Limits and evidence

There is no seed option for reproducible output. Preserve generated benchmark inputs
and checksums. Line count does not specify an exact file size. No Unicode/emoji data
mode, stdout streaming, pager or styled table is implemented; the output is ASCII
test data and the completion message is plain text. There is no direct GNU Coreutils
counterpart. U1/U9 have direct [library](tests/library.rs), [CLI](tests/cli.rs), and
compiled API-example coverage, plus shared runtime tests; this is not a complete
U1–U9 audit or a performance claim. Other universal requirements remain unaudited.
The Linux-only `/dev/full` regression checks buffered flush failure; local macOS
runs cannot execute it. Shared overhead evidence is [recorded separately](../utils/cli-support/overhead.md).

[Workspace README](../README.md)
