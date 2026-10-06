# Shared setup overhead

Local measurements from 2026-10-05, comparing `cb4f703` with `c646abd` where noted.
Host: macOS 27.0.1 (26A434), arm64, rustc 1.101.0-nightly
(db8f076d2 2026-10-03), release profile and default features. No repository
builds/tests ran during measurement. This was an uncontrolled workstation;
CPU model, memory size and cache/storage controls were unavailable.

Raw captures and the one-off startup probe are not retained in the source tree.
These observations describe that development run, not a portable performance claim.

## Stage collection

The [Rust harness](benches/overhead.rs) checksums a fixed 4 KiB buffer of bytes equal
to 7, 100,000 times per sample. Every sample must return 2,867,200,000. Each mode
had three warm-up rounds and six reported rounds, run sequentially. Initialization
was outside timing. Debug text went to `/dev/null`, including formatting, checked
writes and system calls in the measured cost.

| Mode | Median ns/operation | Min–max ns/operation |
| --- | ---: | ---: |
| bare | 168.71 | 168.62–171.40 |
| disabled | 168.74 | 168.29–168.97 |
| debug | 1506.62 | 1459.45–1630.76 |

Disabled spans were within noise of bare work. Debug collection added about
1.34 microseconds per stage. Use the [benchmark commands](README.md#verification-and-overhead)
to collect fresh samples for a future change.

## Small-command startup

A one-off probe alternated retained before/after Biggie binaries: five warm-ups
and fifty measured invocations per version. Each invocation generated one line in
a fresh temporary file. Timing included process launch, setup, generation, I/O and
exit. Outside timing, every invocation checked status, stdout, empty stderr and
Biggie's line/word constraints. Random bytes differed because Biggie has no seed API.

| Version | Median ms/process | Min–max ms/process |
| --- | ---: | ---: |
| before (`cb4f703`) | 2.401 | 1.978–2.951 |
| after (`c646abd`) | 2.312 | 1.954–2.909 |

The difference was inconclusive: distributions overlapped and its sign changed
across preliminary reruns. There is no demonstrated startup win or regression.

Full throughput, memory/allocations, terminal rendering cost, Linux performance,
and GNU/BSD comparisons remain unmeasured. See the [measurement procedure](../../docs/benchmarking.md).

## Guardrail harness validation on 2026-10-06

A fresh run at guardrail revision `b1b66bf` used Apple M5 Max (18 logical CPUs,
64 GiB RAM), Darwin 27.0.0, the same dated compiler above, and release/default
features. Raw CSV and hardware metadata are retained locally under
`target/benchmark-evidence/full-host`; the scheduled workflow retains future
artifacts for 30 days. This is an uncontrolled workstation result.

| Mode | Median ns/operation | Min–max ns/operation |
| --- | ---: | ---: |
| bare | 174.67 | 171.43–190.01 |
| disabled | 171.92 | 169.06–174.55 |
| debug | 1492.96 | 1480.26–1507.19 |

All 18 reported samples passed the existing checksum assertion. Each mode had
three warmups and six reported samples, with stderr redirected identically to
`/dev/null`. Initialization and terminal rendering remain outside the measurement;
these timings do not establish memory or startup improvements. This evidence
predates master's later Biggie/shared-CLI merge and is not a measurement of it.
