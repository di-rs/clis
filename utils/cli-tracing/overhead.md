# Shared setup overhead

Measured on 2026-10-05, macOS 27.0.1 (26A434), arm64, rustc
1.101.0-nightly (db8f076d2 2026-10-03), release profile and default features.
No repository builds/tests ran during measurement. CPU model and memory size were
unavailable in the sandbox; this is an uncontrolled workstation, not a dedicated
performance runner. Temporary data used the local system temporary filesystem;
no cache controls or storage profiling were applied.

## Repeated stage collection

The [Rust harness](benches/overhead.rs) checksums a fixed 4 KiB buffer of bytes equal
to 7, 100,000 times per sample. Every sample must return 2,867,200,000. Each process
has three warm-up rounds and six reported rounds. Construction/global installation
happens before timing. The same checksum runs bare, with a disabled debug span,
and with debug span-close formatting. Text stderr is redirected to `/dev/null`;
formatting, checked writes, and system-call costs are included. Modes run sequentially.

| Mode | Median ns/operation | Min–max ns/operation |
| --- | ---: | ---: |
| bare | 168.71 | 168.62–171.40 |
| disabled | 168.74 | 168.29–168.97 |
| debug | 1506.62 | 1459.45–1630.76 |

Disabled spans are within measurement noise of bare work. Debug collection costs
about 1.34 microseconds more per stage in this workload. This is instrumentation
cost, not a speedup or a comparison with the previous JSON-to-memory-sink harness,
whose output and destination performed different work.

[Raw stage samples](benches/results/stages.csv). Reproduce from the workspace root:

```sh
cargo bench --locked -p cli-tracing --bench overhead -- --bare --log-level=off
cargo bench --locked -p cli-tracing --bench overhead -- --log-level=off
cargo bench --locked -p cli-tracing --bench overhead -- --log-level=debug 2>/dev/null
```

The recorded runs executed the built benchmark directly after
`cargo bench --locked -p cli-tracing --bench overhead --no-run --message-format=json`;
Cargo build/launch messages are excluded from the CSV.

## Small-command startup

Compare a retained release `biggie` at base `cb4f703` with the focused setup.
The [Python runner](benches/startup.py) alternates process order, performs five
warm-ups per version, then records fifty samples each. Every invocation creates
one line in a fresh temporary output file. The timed region includes process
launch, Clap/setup, random generation, writing/flushing, captured stdout/stderr,
and waiting for exit. It is not a pure subscriber-initialization measurement.

Outside timing, every invocation checks status 0, exact completion stdout, empty
stderr, one newline, and the documented ASCII word/count/length constraints.
Random bytes differ; there is no seed API. Both versions perform the same bounded
one-line workload, and each starts without a destination file.

| Version | Median ms/process | Min–max ms/process |
| --- | ---: | ---: |
| before | 2.401 | 1.978–2.951 |
| after | 2.312 | 1.954–2.909 |

[Raw startup samples](benches/results/startup.csv). The small difference is
inconclusive: distributions overlap and its sign changed across preliminary reruns.
There is no demonstrated startup win or regression. The current setup adds global
initialization but removes configuration paths; these results do not isolate them.

Reproduce after building each revision separately and retaining the base binary:

```sh
python3 utils/cli-tracing/benches/startup.py /private/tmp/clis-focused-biggie-before target/release/biggie
```

Recorded executable SHA-256 values:

- Before: `d3c92b5f9515c71c47d2081075da7e1bc0ceec1b45427f290e18664ceb50abec`.
- After: `ac84ae165cc24aff0f5aff293dc5e5f385baa8d4ade00f097269ed91dac3b5f9`.

The candidate is the change after `cb4f703`. SHA-256 of the concatenated files
below, in order, is `ef3e4e372bc26a42dd2796275038c33f5eb2aa4fec1f5ddbf6e33a930897af00`:

- `Cargo.toml`
- `Cargo.lock`
- `utils/cli-tracing/src/lib.rs`
- `utils/cli-tracing/src/sink.rs`
- `utils/cli-tracing/benches/overhead.rs`
- `biggie/Cargo.toml`
- `biggie/src/cli.rs`
- `biggie/src/lib.rs`
- `biggie/src/main.rs`

Full throughput, memory/allocations, terminal rendering cost, Linux performance,
and GNU/BSD comparisons remain unmeasured. These results do not certify utility
performance or justify hot-path logging. See the [measurement procedure](../../docs/benchmarking.md).
