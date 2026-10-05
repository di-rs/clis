# Benchmarking and performance acceptance

[Project overview](../README.md) · [Compatibility](compatibility.md)

The goal is to match or outperform GNU/BSD reference utilities on representative,
semantically equivalent workloads. This is a measured target, not a promise that
Rust, every utility, or every input will be faster. Keep raw evidence and report
regressions and memory trade-offs as carefully as improvements.

## Correctness gate before timing

Confirm matching stdout bytes, expected status, diagnostics, and relevant side
effects for every benchmark case before measuring it. Both commands must do the
same work under the same options, input, locale, environment, and output policy.
A missing flag, dropped binary data, ignored error, or omitted filesystem work
invalidates the comparison. Apply the selected GNU/BSD conflict policy explicitly.

Do not use blanket Hyperfine `-i`/`--ignore-failure`: it can make a failed command
look fast. Commands such as `false` and grep-like no-match cases legitimately return
nonzero; a case-specific wrapper must validate the exact expected status and fail
on any other outcome. Do not simply append `|| true` or discard failed samples.
Correctness checks and setup belong outside the timed command, unless end-to-end
setup is explicitly the workload being measured.

## Baselines, build, and workload coverage

Build release binaries before invoking Hyperfine; do not time `cargo run` or debug
builds. Compare the new Rust implementation with both the previous Rust revision
and the relevant GNU/BSD reference executable. Use separate output directories or
retained artifacts so a rebuild cannot silently overwrite the old baseline.
Run comparisons on the same host; timings from different machines/OSes cannot be
combined into an implementation speedup. Identify every executable explicitly.

A useful suite covers the applicable dimensions below, not every cross-product:

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
executable path and record the reference version or BSD OS release separately.
Use paths without embedded quotes or shell metacharacters in this shell recipe.

```sh
set -eu
: "${REF_TAIL:?Set REF_TAIL to an absolute GNU or BSD tail executable path}"
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

For performance-sensitive changes, add/update relevant cases and compare before
and after under the same setup. A claimed win requires correctness and repeatable
improvement; report regressions in other cases and explain resource trade-offs.
Do not add complicated fast paths for noise-level gains. A parity fix may cost
more because it now performs required work; disclose that instead of hiding it.

Ordinary shared CI runners are appropriate for correctness and benchmark smoke
checks, not an unexplained hard timing threshold. Dedicated performance jobs and
regression budgets can be added after a stable baseline is established. They are
not configured by this document. When tools/hardware/references are unavailable,
report what remains unmeasured and provide the reproduction recipe; never fabricate
numbers or claim performance verification from a successful build.
