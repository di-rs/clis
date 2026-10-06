# Benchmarking and performance acceptance

[Project overview](../README.md) · [Compatibility](compatibility.md)

The [north star's P4](north-star.md#p4--correctness-checked-benchmarks) sets the target:
beat references on declared priority workloads, with explicit acceptance of measured
regressions. This guide owns the measurement procedure. Every port needs a recipe;
adding the standard does not mean missing recipes or results already exist.

## Correctness gate before timing

Confirm matching stdout bytes, expected status, diagnostics, and relevant side
effects for every benchmark case before measuring it. Both commands must do the
same work under the same options, input, locale, environment, and output policy.
A missing flag, dropped binary data, ignored error, or omitted filesystem work
invalidates the comparison. Select cases from the utility's documented contract.

Do not use blanket Hyperfine `-i`/`--ignore-failure`: it can make a failed command
look fast. Commands such as `false` and grep-like no-match cases legitimately return
nonzero; a case-specific wrapper must validate the exact expected status and fail
on any other outcome. Do not simply append `|| true` or discard failed samples.
Correctness checks and setup belong outside the timed command, unless end-to-end
setup is explicitly the workload being measured.

## Baselines, build, and workload coverage

Build release binaries before invoking Hyperfine; do not time `cargo run` or debug
builds. Compare the new Rust implementation with both the previous Rust revision
and GNU (or the explicitly selected non-GNU baseline). For adopted BSD-only features,
compare equivalent behavior to that named BSD reference where applicable; do not
pass an unsupported flag to GNU and time its failure. Use separate output directories or
retained artifacts so a rebuild cannot silently overwrite the old baseline.
Run comparisons on the same host; timings from different machines/OSes cannot be
combined into an implementation speedup. Identify every executable explicitly.

Declare priority workloads before measuring, with their user purpose and resource
constraints. A useful suite covers the applicable dimensions below, not every cross-product:

| Dimension | Representative cases |
| --- | --- |
| Startup | Empty/tiny valid workloads; process launch overhead matters for small tools. |
| Throughput | Medium/large data; bytes per second and elapsed time. |
| Input shape | Short/long records, binary/Unicode where supported, many small files. |
| Access | Stdin/pipes versus regular seekable files; warm versus cold cache when measured. |
| Options | Common defaults, expensive supported flags, and meaningful combinations. |
| Resources | Peak memory and allocations where relevant; concurrency and retained state. |
| Mutation | Equivalent initial filesystem state restored before every iteration. |

Generate deterministic data once with recorded parameters/checksums. Keep large
inputs and raw local outputs outside tracked fixtures, for example under `target/`.
Use [biggie](../biggie/README.md) for large alphanumeric text workloads when suitable.
Use `--seed` and record its revision, lockfile, platform, and complete command;
the same seed/options reproduce bytes within the same build/platform, not across
arbitrary upgrades. Retain the exact input plus checksum for all compared binaries
and later reruns. Word-count/length ranges support short and long records, and
line-ending options cover LF, CRLF and an unterminated final record. Use explicit
small fixtures or another deterministic generator for binary, Unicode, sorted or
other shapes it does not yet produce; its README records follow-up requirements.
For `mkdir`, `touch`, and other mutating tools, reset the sandbox before each run;
otherwise later runs may measure a cheaper operation. Do not use destructive cache
clearing or privileged machine-wide changes without explicit authorization.
Custom apps use a prior implementation or another justified baseline, not an
invented GNU/BSD original. Kara needs operation-level measurements rather than an
uncontrolled interactive-session comparison.

## Tooling and a correctness-first recipe

[Hyperfine](https://github.com/sharkdp/hyperfine) is the existing CLI timing tool;
Nushell is required only for `.nu` runners. Rust library microbenchmarks can isolate
hot paths after profiling; add a benchmark dependency/target only when needed.
A library microbenchmark does not establish whole-command superiority.

The [tail runner](../scripts/benchmark_tail.py) compares both the candidate and
previous Rust binary against an explicit GNU tail before timing any case. It
retains raw streams, statuses, input and executable checksums, revision/lockfile
identities, generation options and all Hyperfine samples. It rejects missing
references, timeouts, unexpected statuses and byte differences. The scope is
last-lines and last-bytes from files plus last-lines from stdin, with identical
warm-cache inputs, output sinks and pipeline overhead for every binary.

Build candidate and baseline in separate directories with their own lockfiles
and the same dated compiler. Set the following paths/revisions to those actual
builds; the output directory must be empty. Run from the workspace root:

```sh
python3 scripts/benchmark_tail.py \
  --candidate "$CANDIDATE_TAIL" --baseline "$PREVIOUS_TAIL" \
  --generator "$BIGGIE" --reference "$GNU_TAIL" \
  --candidate-revision "$CANDIDATE_SHA" --baseline-revision "$PREVIOUS_SHA" \
  --generator-revision "$CANDIDATE_SHA" \
  --candidate-lockfile Cargo.lock --baseline-lockfile "$PREVIOUS_LOCK" \
  --generator-lockfile Cargo.lock --cases coreutils/tailr/benches/cases.json \
  --output-dir target/benchmarks/tailr
```

Use Hyperfine 1.20.0 and Python 3.11 or later. Add `--smoke` for 1,000 records;
the full fixture has 1,000,000 records generated with Biggie seed 42, four words
of eight characters and LF endings. Both use three warmups and 20 samples.
[The Nushell entry](../coreutils/tailr/benches/tail.bench.nu) forwards these same
arguments (tested with Nushell 0.116.1). Neither runner ignores failures.

The [benchmark workflow](../.github/workflows/benchmarks.yml) runs weekly/manual
Linux timings and native macOS smoke checks. Manual dispatch accepts a baseline
ref; an empty ref uses the candidate's parent commit. Missing or unbuildable
baselines fail explicitly. Each run retains 30-day evidence and reports ratios
against previous Rust and GNU, without a required timing threshold. Hardware and
build metadata accompany the raw samples. The existing tracing harness also
records bare/off/debug CSV with the same redirected stderr sink. These stage
measurements exclude initialization and terminal rendering; they do not measure
startup, allocations or peak memory.

## Reproducibility record

Attach raw results and a readable summary containing candidate/base commit SHAs;
reference name, path, version or OS release; `rustc -Vv`; build profile/features and
flags; CPU, memory, OS/kernel, filesystem/storage; input generator/size/checksum;
complete commands; locale/timezone; output sink; cache policy; thread count;
Hyperfine version, warmups, repetitions; correctness outcome; and memory measurement
method. State any controls that could not be held constant.

Report the chosen time statistic, spread/uncertainty, speedup ratio, and sample
count for each case, not only the best run. Mark small/noisy differences inconclusive
and repeat them on a controlled host. Shell overhead matters for tiny commands;
Hyperfine's shell-free mode is useful when no redirection/pipeline is needed, but
changing invocation mode must not change the compared work. Wall time alone does
not measure allocations or peak memory; collect those separately when claiming them.

## Review gate

For behavior or performance-sensitive changes, add/update relevant cases and compare
before and after under the same setup. A claimed win requires correctness and
repeatable improvement. Record regressions against both the previous Rust revision
and references, their impact, and the reviewer's explicit acceptance of the trade-off.
Documentation of a regression alone is not acceptance. Unmeasured or inconclusive
cases stay visible, and cannot support a performance-success claim.
Do not add complicated fast paths for noise-level gains. A parity fix may cost
more because it now performs required work; disclose that instead of hiding it.

Ordinary shared CI runners are appropriate for correctness and benchmark smoke
checks, not an unexplained hard timing threshold. Dedicated performance jobs and
regression budgets can be added after a stable baseline is established. The current scheduled workflow collects evidence without a timing budget. When tools/hardware/references are unavailable,
report what remains unmeasured and provide the reproduction recipe; never fabricate
numbers or claim performance verification from a successful build.
Documentation-only standards work does not require running timings or manufacturing
benchmark results; report unavailable measurement rather than expanding that scope.

## Instrumentation overhead

Stage timings from [observability](observability.md) identify operations to investigate;
they do not establish a speed improvement. Compare identical work with collection
off and on, then distinguish redirected stderr costs from terminal costs. The shared
[overhead harness](../utils/cli-tracing/README.md#verification-and-overhead) checks
its deterministic result before accepting any timing sample.
