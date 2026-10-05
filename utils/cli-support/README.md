# CLI support

Explicit diagnostic configuration, tracing collection and stage timing for CLI
adapters.
It does not parse arguments, install globals, render application errors, or choose
exit statuses. Domains depend only on `tracing`; applications own policy.

## Usage

Within this workspace, add `cli-support = { path = "../utils/cli-support" }` and
`tracing.workspace = true` to a sibling app. Adjust the path for nested apps.

```rust
use cli_support::{Config, Runtime};

let config = Config { timings: true, ..Config::default() };
let runtime = Runtime::new(&config)?;
tracing::dispatcher::with_default(runtime.dispatch(), || {
    let stage = tracing::info_span!(target: "clis::timing", "scan", records = 3);
    let _entered = stage.enter();
    // Call domain operations inside this scope.
});
runtime.finish()?;
# Ok::<(), std::io::Error>(())
```

Use `Config::resolve(overrides, |key| std::env::var_os(key))` at the CLI boundary
for field-by-field precedence. Overrides are typed and contain no Clap values.
`Runtime::with_writer(&config, writer)` instead accepts an owned `Write + Send`
sink, ignoring the configured destination. Use it for embedding and deterministic
tests. Clone the dispatch into workers and join them before finishing.

The [observability contract](../../docs/observability.md) owns flag/environment
names, defaults, severity meanings, file policy, lifecycle and privacy rules.
[`biggie`](../../biggie/README.md) is the first migrated consumer.

## Records and limits

Events use tracing-subscriber's text/JSON formatter, including event fields and
span context. There is no clock timestamp or ANSI styling. Stage summaries use
`TIMING stage=... elapsed_ms=...` in text; JSON contains `kind: "timing"`, `stage`,
`elapsed_ms` and `fields`. Durations cover span creation to final close, including
idle time. Counts are supplied by the domain; missing counts do not imply zero.
No cross-stage aggregation, timeline export, log-facade bridge, async writer,
rotation or panic/backtrace hook is included.

Sink access serializes whole records. Memory scales with currently open spans,
field values and the largest formatted record; it does not retain prior records.
Do not record unbounded input contents. Sink errors are latched and returned on
`finish()`, including repeated calls. Dropping the runtime alone is not a checked
flush, and a still-live span/dispatch can emit later: stop workers and close spans
before finishing. A scoped dispatch affects only its current synchronous thread.

## Verification and overhead

From the workspace root:

```sh
cargo nextest run --locked -p cli-support -p biggie
cargo test --locked -p cli-support -p biggie --doc
cargo bench --locked -p cli-support --bench overhead
```

The benchmark compares a deterministic 4 KiB checksum without instrumentation,
with timing disabled, and with JSON summaries enabled to `io::sink()`. Every
sample asserts an independently calculated checksum, rotates order and prints
raw timings. It measures steady-state collection/formatting, not startup, disk,
terminal throughput or memory. See [recorded evidence](overhead.md); use real
workloads before claiming a utility or a reference comparison became faster.
