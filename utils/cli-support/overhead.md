# Shared instrumentation overhead

Measured on 2026-10-05 with `cargo bench --locked -p cli-support --bench overhead`.
Environment: macOS 27.0.1 (26A434), arm64, rustc 1.101.0-nightly
(db8f076d2 2026-10-03), Cargo bench/release profile. CPU model was unavailable
to the sandbox. No other repository builds/tests were running during the retained run.

The workload checksums the same 4 KiB buffer (all bytes 7) 100,000 times per
sample. Three warm-up rounds precede six reported rounds, rotating mode order.
Every sample asserts checksum 2,867,200,000 before printing timing. Startup,
allocation of the input, and subscriber construction are outside the timed region.

| Mode | Median ns/operation | Min–max ns/operation |
| --- | ---: | ---: |
| bare | 168.38 | 168.07–173.56 |
| disabled | 167.96 | 167.84–168.32 |
| timings_json_sink | 798.86 | 797.96–804.78 |

Disabled instrumentation is indistinguishable from the bare operation at this
scale; its slightly smaller median is not evidence of an improvement. Enabled
JSON summaries add about 0.63 microseconds per stage in this tiny workload.
That opt-in cost includes clock reads, span storage, formatting and synchronization.
This is not a real-file/terminal benchmark or a GNU comparison. Memory usage,
Linux overhead, full biggie throughput and old/new CLI startup costs remain unmeasured.

The measured code is the shared implementation added after base `0a10b46`.
For reproducibility, SHA-256 of the concatenated bytes of `benches/overhead.rs`,
`src/runtime.rs`, `src/sink.rs`, `src/timing.rs` (in that order, within this crate):
`68e0425f42acdedf58e0a4390a521414e733eac4d290309ffef81512c56f6293`.

## Raw samples

```csv
mode,repetition,operations,elapsed_ns,checksum
bare,0,100000,16816584,2867200000
disabled,0,100000,16809458,2867200000
timings_json_sink,0,100000,79810667,2867200000
disabled,1,100000,16797584,2867200000
timings_json_sink,1,100000,79796042,2867200000
bare,1,100000,16840000,2867200000
timings_json_sink,2,100000,79847375,2867200000
bare,2,100000,16806709,2867200000
disabled,2,100000,16793417,2867200000
bare,3,100000,16835792,2867200000
disabled,3,100000,16831750,2867200000
timings_json_sink,3,100000,79923833,2867200000
disabled,4,100000,16783917,2867200000
timings_json_sink,4,100000,80054875,2867200000
bare,4,100000,16857084,2867200000
timings_json_sink,5,100000,80477583,2867200000
bare,5,100000,17356459,2867200000
disabled,5,100000,16792667,2867200000
```

[Harness and interpretation](README.md#verification-and-overhead)
