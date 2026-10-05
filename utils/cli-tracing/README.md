# cli-tracing

A CLI-side adapter for diagnostic events and opt-in stage timings. It resolves
configuration, builds a `tracing` subscriber, and checks diagnostic write/flush
failures. `TracingSession` owns that subscriber and its sink; it is not an async
runtime and creates no worker threads.

Use this crate when an application needs the workspace's diagnostic policy.
Domain libraries emit through `tracing` and return their own errors; they need no
`cli-tracing` dependency or initialization. Applications already owning a
subscriber can collect those same events and spans themselves.

## Dependencies and boundaries

For a sibling application in this workspace:

```toml
[dependencies]
cli-tracing = { path = "../utils/cli-tracing" }
tracing.workspace = true
# Only if the application parses arguments or accepts log-facade records:
clap.workspace = true
log.workspace = true
tracing-log.workspace = true
```

Adjust the path for nested applications. This is a workspace crate, not an
instruction to install a published package.

| Concern | Owner |
| --- | --- |
| Operations, typed errors, event/span instrumentation | Domain library |
| Argument parsing, error context, diagnostics and exit status | CLI adapter |
| Typed settings, subscriber construction, checked sink | `cli-tracing` |
| Global subscriber/logger installation, worker lifecycle | Application host |

Clap, `log`, and `tracing-log` are only development dependencies of this crate,
used to verify the integrations below. There is no required Clap trait, `log`
wrapper, shared application error type, or automatic process initialization.
`anyhow` remains a separate choice at the CLI orchestration boundary.

## Choose a setup

Logging and timing are independent. No Cargo feature selection is needed.

| Need | Setup |
| --- | --- |
| Library use under a host's subscriber | Depend on `tracing`; do not create a session |
| No diagnostics or timing | Default config: level `off`, timings `false` |
| Diagnostic events only | Set `level`; leave `timings: false` |
| Stage durations only | Set `timings: true`; leave level `off` |
| Both | Set `level` and `timings: true` |
| Embedding, isolated synchronous call, test | `with_default(session.dispatch(), operation)` |
| Standalone app with process-wide collection | Install a global dispatch explicitly, once |
| An owned writer instead of stderr/file | `TracingSession::with_writer(&config, writer)` |

### Programmatic and environment configuration

Construct `Config` directly when the host supplies policy. This does not read the
environment. This example collects both events and timings within one scope:

```rust
use cli_tracing::{Config, TracingSession};
use tracing::level_filters::LevelFilter;

let config = Config {
    level: LevelFilter::INFO,
    timings: true,
    ..Config::default()
};
let session = TracingSession::new(&config)?;
tracing::dispatcher::with_default(session.dispatch(), || {
    let stage = tracing::info_span!(target: "clis::timing", "scan", records = 3);
    let _entered = stage.enter();
    tracing::info!("scan completed");
}); // Stage spans close before finish.
session.finish()?;
# Ok::<(), std::io::Error>(())
```

For environment-only integration, resolve an empty `Overrides` at the application
boundary. Tests can inject a lookup without modifying the process environment:

```rust
use cli_tracing::{Config, Overrides};

let config = Config::resolve(Overrides::default(), |key| std::env::var_os(key))?;
# Ok::<(), cli_tracing::ConfigError>(())
```

Each field resolves independently: explicit override, supplied environment, then
default. An overridden invalid environment value is ignored; an effective invalid
value is an error. `RUST_LOG` and target-specific filter expressions are not read.

| Override | Environment | Accepted values; default |
| --- | --- | --- |
| `level` | `CLIS_LOG_LEVEL` | `off`, `error`, `warn`, `info`, `debug`, `trace`; `off` |
| `format` | `CLIS_LOG_FORMAT` | `text`, `json`; `text` |
| `destination` | `CLIS_LOG_FILE` | Native filesystem path or `-` for stderr; stderr |
| `timings` | `CLIS_TIMINGS` | `true`, `false`; `false` |

### Clap integration

Clap stays in the application. A local `#[derive(clap::Args)]` group can be
included in a parser with `#[command(flatten)]`. It produces `Overrides`, then
`Config::resolve` applies environment values and defaults. See the complete,
executable [Clap example](examples/clap.rs), including precedence tests.

Keep the parsed fields as `Option<T>` and do not set ordinary Clap defaults on
them: defaults would look like explicit overrides and hide environment values.
For a boolean, `Option<bool>` distinguishes absence from explicit `false`;
the example accepts both `--timings` and `--timings=false`. It maps
`--log-file=-` to `Destination::Stderr` explicitly.

The crate deliberately does not add `-v`, `-q`, or other flags. The application
must preserve its reference command's meanings. Existing custom verbosity aliases
can be mapped to `Overrides.level`; [biggie's adapter](../../biggie/src/cli.rs)
shows explicit `--log-level` taking precedence over its existing aliases.

Run from the workspace root (these examples write no data to stdout):

```sh
cargo run --locked -p cli-tracing --example clap -- --log-level=info
CLIS_LOG_LEVEL=debug cargo run --locked -p cli-tracing --example clap -- --log-level=off --timings --log-format=json
CLIS_TIMINGS=true cargo run --locked -p cli-tracing --example clap -- --timings=false
```

### Accepting the log facade and choosing a global subscriber

Yes: ordinary `log::error!`, `warn!`, `info!`, `debug!`, and `trace!` records can
reach this subscriber through [`tracing-log::LogTracer`](https://docs.rs/tracing-log/0.2.0/tracing_log/struct.LogTracer.html).
The application adds `log` and `tracing-log` dependencies and calls
`tracing_log::LogTracer::init()` once. This installs the process-global `log`
logger; `cli-tracing` never installs it on the caller's behalf.

The bridge forwards to the active tracing dispatch. It works with a scoped
dispatch; outside that scope, records need another active subscriber to collect
them. A standalone application may instead explicitly call
`tracing::dispatcher::set_global_default(session.dispatch().clone())` once.
The executable [log bridge example](examples/log-bridge.rs) demonstrates both
global installations, all five severities, native tracing events, and a worker:

```sh
cargo run --locked -p cli-tracing --example log-bridge
```

A second installation returns an error; it cannot replace an existing logger or
subscriber. An embedded library must use the host's setup. Keep the session alive
until workers stop and call `finish()` before application exit; a global dispatch
cannot be uninstalled by dropping the session.

“Everything” still means records that survive the producer's compile-time
filters, `log`'s maximum level, and `Config.level`. The bridge initializes the
`log` maximum to `Trace`; the session's configured threshold still applies.
It cannot restore suppressed records or create stage spans from log messages.
Use `tracing` for structured domain fields and stage timing. With this crate's
formatter features, bridged source metadata appears as `log.target`, `log.module_path`
and `log.file` fields, with top-level target `log`; native tracing records retain
their own target. Arbitrary `log` key-value extensions are not a supported schema.
Do not enable a reverse tracing-to-log bridge alongside this direction.

### Custom sinks and worker threads

`with_writer` takes ownership of a `Write + Send + 'static` writer and ignores
`Config.destination`. It performs no I/O during construction. A scoped dispatch
is thread-local: explicitly clone it for workers and join them before finishing.
This example uses `io::sink()`; tests can supply an inspectable writer instead.

```rust
use cli_tracing::{Config, TracingSession};
use tracing::level_filters::LevelFilter;

let session = TracingSession::with_writer(
    &Config { level: LevelFilter::INFO, ..Config::default() },
    std::io::sink(),
);
std::thread::scope(|scope| {
    let dispatch = session.dispatch().clone();
    scope.spawn(move || {
        tracing::dispatcher::with_default(&dispatch, || tracing::info!("worker done"));
    });
});
session.finish()?;
# Ok::<(), std::io::Error>(())
```

For async applications, use tracing's future instrumentation and dispatch
propagation; do not hold a span entry guard across `.await`. This crate supplies
no async runtime or task lifecycle management.

## Output and lifecycle contract

`new` opens stderr or creates a diagnostic file exclusively, even if both event
logging and timings are off. Existing files and symlinks are errors. For a fresh
JSON file, set `format: Format::Json` and `destination: Destination::File(path)`.
Relative paths resolve against the process working directory when `new` opens them.
The application must reject aliases between its data file and diagnostic file
before opening data for truncation. `with_writer` leaves destination policy to
the caller.

Events use tracing-subscriber's text/JSON formatter with event fields and ordinary
span context. There are no clock timestamps or ANSI styles. Only spans targeted
at `clis::timing` produce stage summaries; ordinary spans do not. Timing spans are
collected separately from event context, independent of the event severity level.

| Format | Stage record |
| --- | --- |
| Text | `TIMING stage=scan elapsed_ms=1.250` followed by recorded fields |
| JSON | `{"kind":"timing","stage":"scan","elapsed_ms":1.25,"fields":{"records":3}}` |

Durations cover span creation to final close, including waiting; they are not CPU
time and overlapping spans cannot be summed as total runtime. Fields can be
updated before close. Counts come from the domain; an absent count does not mean
zero. Disabled timing spans have no timer or retained fields under this subscriber.

Always close spans, stop/join workers, and call `finish()`, including when the
operation failed. `finish()` flushes the sink and returns the first write/flush
error, even if a tracing callback swallowed it. Repeated calls retain that error.
Dropping a session is not checked finishing; `finish()` neither unregisters
cloned dispatches nor prevents later writes, and does not `fsync` files.
If both the operation and diagnostics fail, preserve both errors. See
[biggie's orchestration](../../biggie/src/main.rs) for the complete error path.

Required command errors remain independent of optional logging. The
[observability contract](../../docs/observability.md) owns severity policy, privacy,
exit behavior, and workspace flag conventions.

## Why these modules exist

One package keeps configuration, subscriber ownership, and checked output together.
Callers enable only the capabilities they need; separate logging and timing
packages would not remove their shared sink and lifecycle requirements.

| Module | Necessary work |
| --- | --- |
| `config` | Typed settings, strict parsing, injected environment, explicit precedence |
| `session` | Construct filtered event/timing layers and expose an owned dispatch |
| `sink` | Serialize records and retain failures that formatter callbacks cannot return |
| `timing` | Collect final span fields and numeric elapsed milliseconds in the stage schema |

Formatting ordinary events, dispatch, spans, and filtering use upstream `tracing`
and `tracing-subscriber`. The custom timing layer exists for the elapsed-duration
schema above. Tracing-subscriber's built-in span-close records are an alternative,
but report busy/idle duration strings and have a different JSON shape.

There is no queue, rotation, history, aggregation, timeline export, panic hook,
backtrace setup, or error-reporting framework. Writes are synchronous. Memory
scales with live spans, their fields and formatted records, not prior records.
Record bounded metadata rather than input contents. Diagnostic I/O and enabled
formatting still cost time; stage timings are not benchmark evidence by themselves.

## Verification and overhead

From the workspace root:

```sh
cargo nextest run --locked -p cli-tracing -p biggie
cargo test --locked -p cli-tracing -p biggie --doc
cargo test --locked -p cli-tracing --example clap
cargo build --locked -p cli-tracing --examples
cargo doc --locked -p cli-tracing --no-deps
cargo bench --locked -p cli-tracing --bench overhead
```

Tests cover precedence, severity thresholds, scoped log bridging, JSON escaping,
final timing counts, disabled field evaluation, file collisions, writer/flush
failures, and explicit worker propagation. Biggie tests exercise an actual CLI
consumer and direct domain calls without initialization.

The benchmark compares the same deterministic 4 KiB checksum without
instrumentation, with timing disabled, and with JSON timings sent to `io::sink()`.
Every sample checks its result. See [raw measurements and limits](overhead.md).
Startup, disk/terminal throughput, memory, and full utility/reference performance
need separate workload evidence.
