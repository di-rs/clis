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

Explicit `--log-level` overrides `CLIS_LOG_LEVEL`, which overrides the default off.
Invalid effective settings fail during Clap parsing, before creating data.
The shared setup has no `-v`/`-q` aliases, file/JSON options, or timing switch.
See the [shared contract](../docs/observability.md).

```sh
cargo run --locked -p biggie -- -n 100 biggie-timed.txt --log-level=debug
```

The completion message goes to stdout. Diagnostics use plain text on stderr;
data stays in `FILE`. Successful generation emits an info event through `log`;
debug records the requested count and trace records completion. At debug/trace,
`command`, `generate`, and `flush` spans emit built-in close records with timestamps
and busy/idle duration strings. Generation records completed counts only on success.
There are no per-line or input-content logs.

Errors always go to stderr once, prefixed with the name from Clap. Parsing errors
exit 2; operation, setup, or reported write/flush errors exit 1. Success exits 0
after explicit data/diagnostic flushing. An error can leave a partial data file.
Backtraces are independent of verbosity; the concise reporter displays causes.

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
compiled API-example coverage, plus shared setup process tests; this is not a complete
U1–U9 audit or a performance claim. Other universal requirements remain unaudited.
The Linux-only `/dev/full` regression checks buffered flush failure; local macOS
runs cannot execute it. Shared overhead evidence is [recorded separately](../utils/cli-tracing/overhead.md).

[Workspace README](../README.md)
