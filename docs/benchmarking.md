# Benchmarking

Each finite CLI owns TOML suites under its `benches/` directory.
[Biggie](../biggie/README.md) generates inputs once; [hyperfine 2](https://github.com/sharkdp/hyperfine/releases/tag/v2.0.0)
measures the executables directly, including warmups, timing and peak RSS.
The [runner](../tools/cli-bench/bench.py) requires Python 3.11+ and no Python packages.
On macOS, the system Python may be too old; use an installed Python 3.11+ instead.

## Run a suite

From the workspace root, with `hyperfine` on PATH:

```sh
cargo build --locked --release --workspace --bins
python3 tools/cli-bench/bench.py coreutils/tailr/benches/cli-bench.toml
```

This compares `target/release/tailr` with the suite's system `tail`.
Use `--reference /usr/bin/tail` to choose the macOS BSD executable explicitly,
or `--reference /path/to/gtail` to select another implementation. System tools
on Linux are usually GNU; a system comparison is not a general parity claim.

```sh
python3 tools/cli-bench/bench.py coreutils/tailr/benches/cli-bench.toml --list
python3 tools/cli-bench/bench.py coreutils/tailr/benches/cli-bench.toml \
  --case file-lines --warmup 1 --runs 3 --output /tmp/tail-smoke
```

The output directory must be new. Defaults are three warmups and twenty measured
runs, with results under `target/benchmarks/`. Use `--output` outside `target/`
to keep reports across `cargo clean`. Each case produces hyperfine's native JSON
and Markdown; the directory also contains the suite and run metadata. A failed
run is marked failed, and failed/partial case reports are not published as results.

## Compare with an earlier version

Pass an existing executable with `--baseline`. Both versions use the case's
candidate arguments; system-specific `reference_args` are only for `--reference`
or the suite's default reference. `--candidate` and `--biggie` also accept explicit
executable paths. Compilation is separate from measurement.

For example, build an earlier revision in a separate checkout, using paths that
do not already exist. Replace `HEAD~1` with the desired tag or commit:

```sh
git worktree add --detach /tmp/clis-before HEAD~1
cargo build --locked --release --manifest-path /tmp/clis-before/Cargo.toml \
  --target-dir /tmp/clis-before-target -p tailr
python3 tools/cli-bench/bench.py coreutils/tailr/benches/cli-bench.toml \
  --baseline /tmp/clis-before-target/release/tailr
git worktree remove /tmp/clis-before
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
environment sets `LC_ALL=C`, `TZ=UTC` and `CLIS_LOG_LEVEL=off`; an optional suite
`env` table can override these values.

## Verification and scope

```sh
python3 -m unittest discover -s tools/cli-bench -p 'test_*.py' -v
```

The [workflow](../.github/workflows/cli-bench.yml) checks runner changes and runs
one-sample suite smoke checks on Linux and macOS. Manual dispatch runs the chosen
suite with normal measurement settings and saves reports as workflow artifacts.
Hosted smoke measurements verify execution; use a quiet local machine for
performance conclusions, inspect variance, and repeat meaningful differences.

Kara is interactive and has no finite CLI benchmark here. Suites without a
portable system-output match use previous-version comparisons. There is no custom
statistics engine, build cache, history database, replay format or PR-comment bot.

The runner follows hyperfine's [manual](https://github.com/sharkdp/hyperfine/blob/v2.0.0/doc/hyperfine.1)
for direct commands, preparation, input and exports. See [Testing](testing.md)
for behavioral regressions.
