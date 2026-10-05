# Shared instrumentation overhead

Measured on 2026-10-05 with `cargo bench --locked -p cli-tracing --bench overhead`.
Environment: macOS 27.0.1 (26A434), arm64, rustc 1.101.0-nightly
(db8f076d2 2026-10-03), Cargo bench/release profile. CPU model was unavailable
to the sandbox. No other repository builds/tests were running during the retained run.

The workload checksums the same 4 KiB buffer (all bytes 7) 100,000 times per
sample. Three warm-up rounds precede six reported rounds, rotating mode order.
Every sample asserts checksum 2,867,200,000 before printing timing. Startup,
allocation of the input, and subscriber construction are outside the timed region.

| Mode | Median ns/operation | Min–max ns/operation |
| --- | ---: | ---: |
| bare | 168.15 | 167.96–169.27 |
| disabled | 168.42 | 167.82–169.16 |
| timings_json_sink | 810.10 | 806.31–816.62 |

Disabled instrumentation is indistinguishable from the bare operation at this
scale; the observed difference is not evidence of an improvement. Enabled
JSON summaries add about 0.64 microseconds per stage in this tiny workload.
That opt-in cost includes clock reads, span storage, formatting and synchronization.
This is not a real-file/terminal benchmark or a GNU comparison. Memory usage,
Linux overhead, full biggie throughput and CLI startup comparisons remain unmeasured.

The measured code is the shared implementation revised after base `f246216`.
For reproducibility, SHA-256 of the concatenated bytes of `benches/overhead.rs`,
`src/session.rs`, `src/sink.rs`, `src/timing.rs` (in that order, within this crate):
`4590ec612fe499e29b6fe516c05e99be3f5fc3154f78839c786785f264df9319`.

## Raw samples

```csv
mode,repetition,operations,elapsed_ns,checksum
bare,0,100000,16796291,2867200000
disabled,0,100000,16804292,2867200000
timings_json_sink,0,100000,80631167,2867200000
disabled,1,100000,16868833,2867200000
timings_json_sink,1,100000,81662333,2867200000
bare,1,100000,16846417,2867200000
timings_json_sink,2,100000,81202125,2867200000
bare,2,100000,16927125,2867200000
disabled,2,100000,16782333,2867200000
bare,3,100000,16806041,2867200000
disabled,3,100000,16827250,2867200000
timings_json_sink,3,100000,80934834,2867200000
disabled,4,100000,16916042,2867200000
timings_json_sink,4,100000,81085375,2867200000
bare,4,100000,16802125,2867200000
timings_json_sink,5,100000,80792916,2867200000
bare,5,100000,16823167,2867200000
disabled,5,100000,16856000,2867200000
```

[Harness and interpretation](README.md#verification-and-overhead)
