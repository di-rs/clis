# Biggie generation measurements

Measured 2026-10-06 on Apple M5 Max, Mac17,14, 64 GiB RAM, macOS 27.0.1
(build 26A434), Darwin arm64. Toolchain: `rustc 1.101.0-nightly
(db8f076d2 2026-10-03)`; Hyperfine 1.20.0. These are local measurements,
not Linux evidence or a general performance guarantee.

The measurements below predate the optional `csv` Cargo feature. They identify
that earlier candidate exactly; they are not fresh timings of subsequent edits.
Default builds still enable CSV. Feature configurations are described in the
[README](README.md#cargo-features).

## Versions and correctness gates

Baseline: `b3b80d24557203acf1e9128fcda7a58268787e69`, retained release binary
`/private/tmp/biggie-before-b3b80d2`. Candidate: the pre-feature Biggie source recorded below
on that base, built with `cargo build --release --locked -p biggie`.

| Identity | SHA-256 |
| --- | --- |
| Baseline binary | `bd003ee05911999569cb2e226a0cea00f623ee88038ccb4a91b2e617c030144d` |
| Candidate binary | `4a6b0948921820650176a44093640b761141beafac1b63011d5806a710d22203` |
| Candidate Cargo.lock | `84ee988bdf03137f2810d7d4beb80ade635cb7d5e19dc2d446b1929675ee9f40` |
| Candidate source manifest | `367a13fd3b0576587dbec58d6866a711ab4a628516cfa9b180a401f559d05723` |

The source identity hashes the UTF-8 `sha256  relative-path` manifest of the
workspace manifest, lockfile, Biggie manifest and all Biggie Rust source files.
The local manifest and raw measurements are retained under `target/biggie-evidence/`.

Before timing, the checker compared the entire seeded million-line ASCII output
with the baseline: identical bytes, 1,000,000 records, 7–14 words per record and
2–11 ASCII alphanumeric bytes per word. Other checks assert literal schedules,
CSV parsing and exact cell counts, byte budget/replay, the custom alphabet and
sorted pair multiset overlap. All subprocess statuses and diagnostics are checked.

| Dataset | Bytes | SHA-256 |
| --- | ---: | --- |
| ascii.txt | 78,748,590 | `0648b3c12e163277893e9352c55686bb3b53e55d345d926bfb616a95c1a13cef` |
| records.txt | 200,000 | `84705daef8424f372386e78f44a47a4a728b30139c3d0274a05eeadcf6e41816` |
| fields.csv | 2,250,281 | `4cd3a0acb5c95b3c5f98ea35cdaf8b82ffbfb812a94360114c72bed76638980b` |
| bytes.bin | 16,777,216 | `07032e29fa1c3531a49548675065b34c322a4c84376d902cb004017619cf3a20` |
| dna.txt | 7,863,478 | `be0120879f5b231d0eb68cb716edc0c68d49fc8f4239483f1639721093a9b858` |
| left.txt | 960,000 | `e385569c68a8c755846d27a49aa048fe67a2448fabe8ec2d9b631ca463abc15c` |
| right.txt | 960,000 | `b549d9a3daed7a0b639539f4ee661a47fc6290302ae5ebd1e32769c8f9c2c619` |

## Existing ASCII workload

Priority workload: default text shapes with seed 42 and one million lines, useful
for existing CLI throughput inputs. Both binaries write to `/dev/null`; completion
stdout and logging stderr are also redirected there. Run with `LC_ALL=C TZ=UTC`,
two warmups and 20 measured runs per binary/level, no concurrent build/test job.

```sh
for level in off debug trace; do
  LC_ALL=C TZ=UTC hyperfine --warmup 2 --runs 20 \
    --command-name "before-$level" \
    "/private/tmp/biggie-before-b3b80d2 --seed 42 -n 1000000 --log-level $level /dev/null > /dev/null 2> /dev/null" \
    --command-name "after-$level" \
    "target/release/biggie --seed 42 -n 1000000 --log-level $level /dev/null > /dev/null 2> /dev/null"
done
```

The recorded run used one Hyperfine invocation with all six commands in this order.
Time is mean ± sample standard deviation; the change column compares means.

| Logging | Baseline ms | Candidate ms | Mean change |
| --- | ---: | ---: | ---: |
| off | 246.7 ± 5.1 | 246.1 ± 2.1 | -0.3% |
| debug | 245.8 ± 4.4 | 245.7 ± 1.3 | -0.1% |
| trace | 246.3 ± 4.9 | 247.1 ± 5.5 | +0.4% |

No consistent elapsed-time change is distinguishable from these runs' variability.
Trace's small positive mean difference is inconclusive, not an accepted regression
or a speedup claim. Hyperfine flagged outliers in several series; all samples remain
in the raw JSON. No cold-cache, terminal-rendering, disk-throughput or CPU-isolation
claim is made. Candidate user CPU time is approximately 5–6 ms higher while system
time is approximately 5–6 ms lower; elapsed parity does not imply equal CPU work.

Early probes exposed roughly 7% overhead. The final implementation uses a concrete
buffered CLI destination, selects the text alphabet outside the hot word loop, and
uses a fixed 64 KiB CLI output buffer for single-stream commands. This trades 56 KiB
of additional CLI buffering for fewer writes, preserving domain output and bounded
memory. Pair retains one default buffer per destination. Preliminary raw results
are retained alongside the final measurements; they describe superseded builds.

## New data shapes

These capabilities have no prior Biggie implementation or direct GNU/BSD baseline.
The results establish local timings, not relative performance claims. Two warmups,
10 measurements, `LC_ALL=C TZ=UTC`, explicit `--log-level off`, stdout/stderr
redirected to `/dev/null`. Arguments below follow `target/release/biggie`.

| Shape | Arguments | Mean ± σ ms | Peak RSS MiB |
| --- | --- | ---: | ---: |
| records | `records /dev/null -r miss -r Hit -r '' -p 2 -c 10000` | 2.2 ± 0.2 | 3.16 |
| csv | `fields /dev/null -n 100000 -F csv -d , -s 42` | 20.7 ± 0.5 | 3.19 |
| bytes | `bytes /dev/null -b 16777216 -s 42` | 7.3 ± 0.2 | 3.14 |
| dna | `text /dev/null -n 100000 -A ACGT -s 42` | 36.0 ± 0.6 | 3.19 |
| pair | `pair -l target/biggie-evidence/left.txt -r target/biggie-evidence/right.txt -a 10000 -j 10000 -b 10000 -c 2` | 5.6 ± 0.1 | 3.09 |

Pair writes two regular files, truncating them each run: each is 40,000 records,
with 20,000 shared occurrences and 20,000 exclusive occurrences per side. This
includes normal cached filesystem writes, not durable `fsync` or cold storage.
The records case is under 5 ms and startup/shell calibration limits its precision;
do not interpret its small timing differences. Other shapes perform different work
and are not meaningfully ranked against each other.

Peak RSS is a separate single `/usr/bin/time -l` process measurement per shape,
reported in bytes by macOS and converted to MiB above. The sandbox initially blocked
its clock query; the successful measurements ran with approved local access.
These samples demonstrate the listed workloads only. Static bounds and tests,
rather than these samples, cover large words, 8 MiB rows/corpora and overflow.
No allocation profiling or memory-scaling study was performed.

## Validation and retained evidence

Final verification passed: 62 Biggie tests, seven Biggie doctests, 767 workspace
tests, workspace doctests, package/workspace Clippy with all targets/features,
formatting, generated Biggie API docs and `git diff --check`. No tests were skipped
in this macOS run; Linux-conditional cases such as `/dev/full` were not compiled
or executed. No native Linux result is claimed.

Local raw evidence (ignored, retained with this worktree):

- `target/biggie-evidence/check_inputs.py`, `checks.log`, `checksums.json` and datasets.
- `target/biggie-evidence/ascii-timings.json`, `ascii-hyperfine.log`, `ascii-benchmark-command.txt`.
- `target/biggie-evidence/shape-timings.json`, `shape-hyperfine.log`, `shape-benchmark-command.txt`.
- `target/biggie-evidence/*-memory.txt`, `hardware.txt`, `source-sha256.txt`.
- `/private/tmp/biggie-final-verify.log` contains the final check workflow.

The [published raw record](benches/results/2026-10-06/measurements.json) retains
all final timing samples, dataset checksums, memory output and feature-build sizes.
The [source manifest](benches/results/2026-10-06/source-sha256.txt) identifies the
timed source, and the [correctness checker](benches/results/2026-10-06/check-inputs.py)
records the executed gate (run from the workspace root with its recorded binaries
and `target/biggie-evidence/` directory available). Large datasets, binaries,
preliminary timings and full check logs remain local and may be lost if the
worktree or temporary files are removed.
See the [CLI/API contract](README.md) and [workspace measurement policy](../docs/benchmarking.md).

## Optional CSV feature follow-up

The later Cargo-feature change was checked on the same local macOS host and
toolchain. Release builds with `--no-default-features` and with
`--no-default-features --features csv` produced binaries of 1,625,296 and
1,665,696 bytes respectively (40,400 bytes difference, about 2.4%). These are
executable file sizes, not resident memory or a throughput measurement.

Both `cargo tree --edges normal,build` and Cargo's JSON compiler-artifact output
confirmed that `csv` and `csv-core` were absent from the disabled Biggie build.
Their presence in the workspace lockfile remains expected. Four single-stream
commands produced identical seeded/literal bytes across the two configurations.
Default/explicit-CSV suites passed 64 tests; the no-CSV suite passed 61 tests,
including rejection before file effects. Seven doctests passed in both modes.
The default workspace passed 769 tests plus doctests; package/workspace Clippy,
no-CSV Clippy, formatting, generated API docs and diff checks passed.

Raw feature-build artifacts remain in `target/biggie-feature-evidence/`;
verification logs are `/private/tmp/biggie-feature-final-verify.log` and
`/private/tmp/biggie-feature-matrix.log`. The new CI step scripts were executed
locally and checked with `bash -n`, and the workflow parsed as YAML. Standalone
`actionlint` and `zizmor` were unavailable locally; CI/Linux execution is pending.
