# Benchmarking

Each finite CLI owns TOML suites under its `benches/` directory.
Each CLI has its own Cargo package, with matching package and binary names;
see the [repository rules](../AGENTS.md).
Run `uv run --locked cli-check benches` to check suite presence, ownership and
case structure without executing benchmarks. A CLI without a finite benchmark
records a reason in its manifest's `package.metadata.cli-check.benchmark-exemption`;
see [cli-check](../tools/cli-check/README.md#record-a-benchmark-exemption).
[Biggie](../packages/biggie/README.md) generates inputs once; [hyperfine 2](https://github.com/sharkdp/hyperfine/releases/tag/v2.0.0)
measures the executables directly, including warmups, timing and peak RSS.
The [runner](../tools/cli-bench/bench.py) requires Python 3.15+ and uses only the
standard library. The root [uv workspace](testing.md#python-tooling) manages its
interpreter and development checks.

## Run a suite

From the workspace root, with `uv` and `hyperfine` on PATH:

```sh
cargo build --locked --release --workspace --bins
uv run --locked python tools/cli-bench/bench.py buildins/tailr/benches/cli-bench.toml
```

This compares `target/release/tailr` with the suite's system `tail`.
Use `--reference /usr/bin/tail` to choose the macOS BSD executable explicitly,
or `--reference /path/to/gtail` to select another implementation. System tools
on Linux are usually GNU; a system comparison is not a general parity claim.

```sh
uv run --locked python tools/cli-bench/bench.py buildins/tailr/benches/cli-bench.toml --list
uv run --locked python tools/cli-bench/bench.py buildins/tailr/benches/cli-bench.toml \
  --case file-lines --warmup 1 --runs 3 --output /tmp/tail-smoke
```

The output directory must be new. Defaults are three warmups and twenty measured
runs, with results under `target/benchmarks/`. Use `--output` outside `target/`
to keep reports across `cargo clean`. Each case produces hyperfine's native JSON
and Markdown; the directory also contains the suite and run metadata. A failed
run is marked failed, and failed/partial case reports are not published as results.

On Linux and macOS, each generator, shell hook, preflight command and complete
hyperfine case has a five-minute deadline. Use `--timeout 600` to allow ten minutes
per phase for longer benchmarks; this includes all warmups and measurements in
each hyperfine case. The version probe is capped at five seconds. A timeout or
interruption kills the command's process group, including shell and hyperfine
children, and records the failure in the run metadata.

## Compare with an earlier version

Pass an existing executable with `--baseline`. Both versions use the case's
candidate arguments; system-specific `reference_args` are only for `--reference`
or the suite's default reference. `--candidate` and `--biggie` also accept explicit
executable paths. Compilation is separate from measurement.

For example, build an earlier revision in a separate checkout, using paths that
do not already exist. Replace `HEAD~1` with the desired tag or commit:

```sh
git worktree add --detach /tmp/sanctuary-before HEAD~1
cargo build --locked --release --manifest-path /tmp/sanctuary-before/Cargo.toml \
  --target-dir /tmp/sanctuary-before-target -p tailr
uv run --locked python tools/cli-bench/bench.py buildins/tailr/benches/cli-bench.toml \
  --baseline /tmp/sanctuary-before-target/release/tailr
git worktree remove /tmp/sanctuary-before
```

Build both versions with comparable toolchains, features and flags. An older
binary must support the selected case's arguments. Suites without a suitable
system reference run the candidate alone unless `--baseline` is supplied.

## Write a suite

Paths in cases and generator arguments are relative to one temporary directory.
The runner resolves executable paths before entering it and removes it on exit.
Both versions receive the same generated files. Biggie accepts its ordinary
argument arrays, including `text`, `records`, `fields`, `bytes` and `pair`:

```toml
binary = "tailr"
reference = "/usr/bin/tail"
generate = [
  ["text", "--seed", "42", "--lines", "100000", "input.txt"],
]

[[cases]]
name = "file-lines"
args = ["-n", "100", "input.txt"]

[[cases]]
name = "stdin-lines"
args = ["-n", "100", "-"]
stdin = "input.txt"
```

`generate` is optional. Seed random recipes explicitly. Biggie does not promise
identical random data across versions or platforms; comparisons within a run
share the exact same generated input. Temporary inputs are not retained for replay.

Optional case fields:

| Field | Purpose |
| --- | --- |
| `reference_args` | Arguments for the system reference when its spelling differs. |
| `stdin` | File reopened for every invocation; this is seekable stdin, not a producer pipe. |
| `prepare` | Shell command before every preflight, warmup and measured invocation. |
| `check` | Shell assertion after each untimed preflight, for filesystem effects. |
| `expected_exit` | Exact expected exit status; defaults to zero. |

For a command that creates files, reset only its output area with
`prepare = "rm -rf work && mkdir work"`, use arguments under `work/`, and assert
the expected effects with a `check` such as `test -d work/a/b`. Preparation is
outside the measured time. Generator outputs must not be changed by a benchmark.
Suites are trusted code: `prepare` and `check` execute through `/bin/sh`.

The runner checks exit status and compares stdout bytes before timing when a
baseline exists. It also checks the exit statuses in hyperfine's measured results.
This is a small guard against measuring different work, not a replacement for
the CLI's correctness tests. Stderr is not compared. For filesystem effects,
the suite's `check` must assert the behavior that matters.

Arguments are quoted for direct execution with `--shell=none`; shell expansion
and pipelines do not occur in `args`. Output is drained with `--output=pipe` so
programs cannot skip work merely because stdout is `/dev/null`. The default
environment sets `LC_ALL=C`, `TZ=UTC`, `SANCTUARY_LOG_LEVEL=off` and
`SANCTUARY_LOG_STDERR=off`; an optional suite
`env` table can override these values.

## Verification and scope

```sh
uv run --locked pytest
```

The [workflow](../.github/workflows/cli-bench.yml) runs for pull requests targeting
`master`. Drafts skip benchmarks unless the `run-ci` label is applied; see
[draft pull requests](testing.md#draft-pull-requests). Marking a PR ready for review
or adding the override label starts selection. There are no benchmark runs on
pushes, merges, or a schedule. A small selection job examines the whole PR's diff
from its merge base, then Linux and
macOS jobs build only the selected CLI packages from the PR head commit
(plus Biggie when needed). Selection reads manifests and the lockfile from that
same commit, even if the target branch has added packages since the PR branched.
An empty selection skips both benchmark jobs.

| Changed files | Automatic selection |
| --- | --- |
| CLI source, manifest, or build script | That package's suites. |
| Suite TOML | That suite. |
| Biggie implementation | Biggie and suites using generated input. |
| Shared Rust implementations, including CLI logging | Suites whose packages depend on the changed library, directly or transitively. |
| Cargo lockfile | Suites whose packages have changed resolved dependencies. |
| Root Cargo manifest, Rust toolchain, or `.cargo/` configuration | All suites, conservatively. |
| Benchmark runner, selector, benchmark workflow, or Python version pin | All suites. |
| Documentation, correctness tests, or Python development-tool configuration | No native benchmarks. Ordinary CI checks still run. |

Lockfile selection compares each package's dependency graph at the PR merge base
and head. Adding a dependency for one CLI does not select unrelated CLIs. Version,
source, checksum and transitive dependency changes count; comments and Cargo's
dependency-name disambiguation do not. The lockfile does not distinguish active
features, target platforms or development dependencies, so a change within a
CLI's graph can still select it even if that dependency is unused by its release
build. Biggie dependency changes also select suites that generate inputs.
Removed suites are not run; renamed source files count on both sides of the rename.

Automatic checks use one measurement and no warmup. To request full measurements
for specific CLIs, put this standalone line in the PR description:

```text
Benchmark-CLIs: tailr,wcr
```

This overrides automatic selection and runs exactly those CLIs with three
warmups and twenty measurements. `Benchmark-CLIs: all` explicitly selects every
CLI with a suite. `Benchmark-CLIs: auto`, or no field, restores automatic selection.
Unknown names fail with a diagnostic. Editing this field triggers a new run;
unrelated PR-description edits do not rerun benchmarks. The request applies to
subsequent commits until changed back to `auto`, and works for fork contributors
subject to GitHub's normal workflow approval settings.

Reports are saved as workflow artifacts. The
[reporting workflow](../.github/workflows/cli-bench-report.yml) updates one PR
comment with the tested revision, a results table for each CLI, build and suite
duration per OS, and total runner time. It runs trusted default-branch code with
permission to post, while benchmark jobs have read-only permissions. Artifacts
are parsed as data; PR code is not executed by the reporter. Stale revisions do
not overwrite newer results. The reporting workflow becomes active once its
definition is on `master`; that does not trigger benchmarks on merge.

Hosted smoke measurements verify execution; use a quiet local machine for
performance conclusions, inspect variance, and repeat meaningful differences.

## CI runtime

Before selective builds, a [full smoke run](https://github.com/di-rs/sanctuary/actions/runs/38452689584)
of all 19 suites took 72 seconds on Linux and 88 seconds on macOS. Release builds
accounted for 57/63 seconds; suite execution took 5/8 seconds. The jobs overlapped,
so elapsed time was about 90 seconds plus queueing, while their summed runner
time was 160 seconds. These are observed durations, not billing estimates.

Selective builds avoid compiling unrelated CLIs. Exact duration depends mainly
on selected dependencies, runner load, and whether full measurements were
requested. Every results comment reports actual durations so estimates can be
checked against real runs. Full measurements repeat each command 23 times
including warmups, in addition to generation and preflight checks; they do not
repeat compilation for each sample.

Kara is interactive and has no finite CLI benchmark here. Suites without a
portable system-output match use previous-version comparisons. There is no custom
statistics engine, build cache, history database, or replay format.

The runner follows hyperfine's [manual](https://github.com/sharkdp/hyperfine/blob/v2.0.0/doc/hyperfine.1)
for direct commands, preparation, input and exports. See [Testing](testing.md)
for behavioral regressions.
