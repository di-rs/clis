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
line-ending options cover LF, CRLF and an unterminated final record. Biggie also supplies Unicode/custom alphabets, literal record schedules,
delimited/CSV fields, raw byte budgets and sorted overlapping pairs. Use small explicit fixtures
for permanent regressions; its README records each shape and its limits.
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

The existing [tail runner](../coreutils/tailr/benches/tail.bench.nu) is a starting
point, not a validated benchmark harness: its input path still uses `./tailr/`
instead of `coreutils/tailr/`, and it uses `-i`. Repair those issues and add result
validation before using that runner as evidence.

The following POSIX-shell recipe is a local example, not a recorded result. It
requires Python 3 for deterministic fixture generation, Hyperfine, `cmp`, and a
chosen reference `tail`. Run from the workspace root; set `REF_TAIL` to its absolute
GNU executable path and record its version separately.
Use paths without embedded quotes or shell metacharacters in this shell recipe.

```sh
set -eu
: "${REF_TAIL:?Set REF_TAIL to an absolute GNU tail executable path}"
export LC_ALL=C TZ=UTC
cargo build --locked --release -p tailr
out="$(pwd)/target/benchmarks/tailr"
candidate="$(pwd)/target/release/tailr"
mkdir -p "$out"
data="$out/lines.txt"
python3 - "$data" <<'PYDATA'
from pathlib import Path
import sys
Path(sys.argv[1]).write_bytes(b"0123456789abcdef\n" * 1_000_000)
PYDATA
"$REF_TAIL" -n 10 "$data" > "$out/reference.out" 2> "$out/reference.err"
"$candidate" -n 10 "$data" > "$out/candidate.out" 2> "$out/candidate.err"
cmp "$out/reference.out" "$out/candidate.out"
test ! -s "$out/reference.err"
test ! -s "$out/candidate.err"
hyperfine --warmup 3 --runs 20 --export-json "$out/result.json" \
  --command-name reference "\"$REF_TAIL\" -n 10 \"$data\" > /dev/null" \
  --command-name candidate "\"$candidate\" -n 10 \"$data\" > /dev/null"
```

The recipe checks successful statuses through `set -e`, exact stdout through
`cmp`, and empty stderr for this successful case. It covers one warm-cache,
seekable-file scenario only. Repeat the correctness gate for other options,
streaming input, and the previous Rust baseline; no single recipe demonstrates
full compatibility or representative performance.

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
regression budgets can be added after a stable baseline is established. They are
not configured by this document. When tools/hardware/references are unavailable,
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

## Additional Biggie input shapes

From the workspace root, generate disposable inputs outside measured runs:

```sh
cargo run --locked -p biggie -- bytes -b 8193 -p 00ff1b0d0a target/boundary.bin
cargo run --locked -p biggie -- records -r miss -r Hit -p 2 -c 100 target/records.txt
cargo run --locked -p biggie -- fields -F csv -d , -n 100 -s 42 target/fields.csv
cargo run --locked -p biggie -- pair -l target/left.txt -r target/right.txt -a 20 -j 30 -b 10
```

Validate budgets, record/field counts and consumer semantics before timing.
Byte fixtures deliberately include data that some current consumers cannot preserve;
a matching byte count alone does not prove correct output. See Biggie's README for
units, quoting, resource limits and reproducibility scope.
