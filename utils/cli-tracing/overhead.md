# Shared instrumentation overhead

Measured on 2026-10-05 on macOS 27.0.1 (26A434), arm64, rustc 1.101.0-nightly
(db8f076d2 2026-10-03), Cargo bench/release profile. CPU model was unavailable
to the sandbox. No other repository builds/tests ran during measurement.

The same harness checksums a 4 KiB buffer (all bytes 7) 100,000 times per sample.
Three warm-up rounds precede six reported rounds, rotating mode order. Every
sample asserts checksum 2,867,200,000. Input allocation, subscriber construction,
and startup are outside the timed region. JSON records go to `io::sink()`.

Before: the retained release benchmark executable from `8f7360f`, using custom
numeric elapsed-time records. After: built-in span-close records, timestamps,
busy/idle durations, and span context. Both versions ran sequentially on the same
host, with identical domain work; versions were not interleaved. The output work
changes intentionally, so this comparison measures its cost, not equivalent
formatting throughput.

Reproduce each revision from its own checkout with:

```sh
cargo bench --locked -p cli-tracing --bench overhead
```

| Version | Mode | Median ns/operation | Min–max ns/operation |
| --- | --- | ---: | ---: |
| before | bare | 168.76 | 168.28–189.25 |
| before | disabled | 169.00 | 168.30–187.89 |
| before | timings_json_sink | 850.25 | 818.44–945.49 |
| after | bare | 168.40 | 168.25–168.74 |
| after | disabled | 168.55 | 168.06–168.86 |
| after | timings_json_sink | 1354.04 | 1340.86–1385.46 |

Disabled instrumentation remains within noise of bare work. Enabled collection
increases from about 0.85 to 1.35 microseconds per stage: approximately 0.50
microseconds, or 59%, more in this small workload. The after version adds about
1.19 microseconds over bare work. The trade-off buys upstream timing/formatting
and removes the custom collector; it is a measured regression, not a speed claim.
Explicit reviewer acceptance of this cost remains part of PR review.

This is not a real-file/terminal benchmark or a GNU comparison. Memory usage,
Linux overhead, full biggie throughput, startup, and logging-only costs remain
unmeasured. The before run had greater variation; retain the raw samples rather
than treating these medians as universal costs.

The after implementation is the change following `8f7360f`. SHA-256 of the
concatenated bytes of `benches/overhead.rs`, `src/session.rs`, and `src/sink.rs`
(in that order, within this crate):
`25ce661ef0d8a876b9d322195e7f349189e71429dfe853804890a9847c6ace4d`.

## Before: raw samples

```csv
mode,repetition,operations,elapsed_ns,checksum
bare,0,100000,16888333,2867200000
disabled,0,100000,16838250,2867200000
timings_json_sink,0,100000,82602167,2867200000
disabled,1,100000,16861584,2867200000
timings_json_sink,1,100000,82377333,2867200000
bare,1,100000,16867875,2867200000
timings_json_sink,2,100000,81844125,2867200000
bare,2,100000,16828333,2867200000
disabled,2,100000,16830125,2867200000
bare,3,100000,16852792,2867200000
disabled,3,100000,17946208,2867200000
timings_json_sink,3,100000,91791292,2867200000
disabled,4,100000,18789458,2867200000
timings_json_sink,4,100000,94549125,2867200000
bare,4,100000,18925250,2867200000
timings_json_sink,5,100000,87448250,2867200000
bare,5,100000,16884083,2867200000
disabled,5,100000,16939084,2867200000
```

## After: raw samples

```csv
mode,repetition,operations,elapsed_ns,checksum
bare,0,100000,16833208,2867200000
disabled,0,100000,16874417,2867200000
timings_json_sink,0,100000,134628708,2867200000
disabled,1,100000,16806334,2867200000
timings_json_sink,1,100000,136015042,2867200000
bare,1,100000,16874042,2867200000
timings_json_sink,2,100000,134086125,2867200000
bare,2,100000,16846042,2867200000
disabled,2,100000,16860875,2867200000
bare,3,100000,16862541,2867200000
disabled,3,100000,16848834,2867200000
timings_json_sink,3,100000,138545750,2867200000
disabled,4,100000,16886459,2867200000
timings_json_sink,4,100000,134792958,2867200000
bare,4,100000,16830833,2867200000
timings_json_sink,5,100000,136997167,2867200000
bare,5,100000,16824750,2867200000
disabled,5,100000,16830625,2867200000
```

[Harness and interpretation](README.md#verification-and-overhead)
